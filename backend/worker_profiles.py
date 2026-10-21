"""Unmodified ClewdR profiles sharing the caller's existing account lease."""
import fcntl
from functools import lru_cache
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
import re
import threading
import time
import tomllib
from tool_instructions import CONTINUATION


PROFILE = 'client-tools'
IDLE_SECONDS = 600
STARTUP_SECONDS = 20


@lru_cache(maxsize=128)
def session_reset(path, stamp, size, session_hash):
    config = tomllib.loads(Path(path).read_text())
    def matches(item):
        cookie = item.get('cookie') if isinstance(item, dict) else None
        return isinstance(cookie, str) and hashlib.sha256(cookie.encode()).hexdigest() == session_hash
    cookies = [item for item in config.get('cookie_array', []) if matches(item)]
    if cookies:
        resets = [item.get('reset_time', 0) for item in cookies]
        return min(resets) if all(type(value) is int for value in resets) else None
    if any(matches(item) for item in config.get('wasted_cookie', [])):
        return -1
    return None


def sync_sessions(manager):
    if not manager.worker_profiles_enabled:
        return
    for _ in range(2):
        with manager.condition:
            manager.refresh()
            now, changed = time.time(), False
            for account in manager.state['accounts'].values():
                if account.get('node_id', 'node-1') != manager.node_id or account['status'] != 'ready':
                    continue
                try:
                    path = Path(account['directory']) / 'profiles' / PROFILE / 'clewdr.toml'
                    info = path.stat()
                    reset = session_reset(str(path), info.st_mtime_ns, info.st_size, account['session_hash'])
                except (OSError, ValueError, TypeError, KeyError):
                    continue
                if reset is None:
                    continue
                until = now + 300 if reset == -1 else reset + 300 if reset > now else 0
                current = account.get('tools_unavailable_until', 0)
                if (until > now and current <= now) or (not until and current):
                    account['tools_unavailable_until'] = until
                    changed = True
            if not changed:
                return
            try:
                manager.save()
                return
            except RuntimeError as error:
                if not manager.cluster:
                    raise
                from cluster_state import StateConflict
                if not isinstance(error, StateConflict):
                    raise
                manager.refresh()


def state(account):
    manifest = Path(account['directory']) / 'profiles' / PROFILE / 'profile.json'
    try:
        value = json.loads(manifest.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def warm(account):
    current = state(account)
    stamp = current.get('used_at', 0)
    return (type(stamp) in (int, float) and time.time() - stamp < IDLE_SECONDS and
            current.get('ready', True) and type(current.get('port')) is int and 0 < current['port'] < 65536)


def hint_key(account):
    return 'webcc:worker-profile:' + account.get('node_id', 'node-1') + ':' + account['id']


def ready_accounts(manager, accounts):
    local = {a['id'] for a in accounts if a.get('node_id', 'node-1') == manager.node_id and warm(a)}
    remote = [a for a in accounts if a.get('node_id', 'node-1') != manager.node_id]
    if remote and manager.shared_limits:
        try:
            values = manager.shared_limits.client.mget([hint_key(a) for a in remote])
            local.update(a['id'] for a, value in zip(remote, values) if value == b'1')
        except Exception:
            pass
    return local


def publish(manager, account, ready):
    if manager.shared_limits:
        try:
            if ready:
                manager.shared_limits.client.set(hint_key(account), '1', ex=IDLE_SECONDS)
            else:
                manager.shared_limits.client.delete(hint_key(account))
        except Exception:
            pass


@contextmanager
def locked(path, blocking=True):
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def directory(account):
    path = Path(account['directory']) / 'profiles' / PROFILE
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def evict(manager, account, expired=False):
    with locked(directory(account) / 'lifecycle.lock', blocking=False) as acquired:
        if not acquired or manager.inflight.get(account['id']) or manager.cluster and manager.cluster.busy(account['id']):
            return False
        if expired and warm(account):
            return False
        remove(manager, account)
        return True


def local_accounts(manager):
    with manager.condition:
        return [dict(item) for item in manager.state['accounts'].values()
                if item.get('node_id', 'node-1') == manager.node_id]


def headroom(starting=False):
    try:
        available = next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
        return int(available) >= (320 if starting else 256) * 1024
    except (OSError, ValueError, StopIteration):
        return False


def expire(manager):
    if not manager.worker_profiles_enabled:
        return
    for item in local_accounts(manager):
        manifest = Path(item['directory']) / 'profiles' / PROFILE / 'profile.json'
        if manifest.exists() and not warm(item):
            evict(manager, item, expired=True)


def trim(manager, account=None):
    candidates = []
    for item in local_accounts(manager):
        if account and item['id'] == account['id']:
            continue
        manifest = Path(item['directory']) / 'profiles' / PROFILE / 'profile.json'
        if manifest.exists():
            candidates.append((state(item).get('used_at', 0), item))
    keep = max(2, manager.worker_profile_warm) if headroom() else 2
    excess = len(candidates) - keep + bool(account)
    for _, item in sorted(candidates, key=lambda entry: entry[0]):
        if excess <= 0:
            break
        if evict(manager, item):
            excess -= 1


def published_port(manager, name):
    from manager import Problem
    bindings = json.loads(manager.docker('inspect', name))[0]['NetworkSettings']['Ports']['8484/tcp']
    if len(bindings) != 1 or bindings[0]['HostIp'] != '127.0.0.1':
        raise Problem(503, 'Worker profile port must bind to loopback only')
    port = int(bindings[0]['HostPort'])
    if not 0 < port < 65536:
        raise Problem(503, 'Worker profile port is invalid')
    return port


def endpoint(manager, account, profile, purpose='request'):
    if profile is None:
        return account['port']
    from manager import Problem, private_write
    if profile != PROFILE or not manager.worker_profiles_enabled:
        raise Problem(503, 'Worker execution profile is unavailable')
    path = directory(account)
    began = time.monotonic()
    with locked(path / 'lifecycle.lock'):
        source = (Path(account['directory']) / 'clewdr.toml').read_text()
        config = source
        for key, value in {'web_search': 'false', 'custom_prompt': json.dumps(CONTINUATION)}.items():
            pattern = r'(?m)^' + key + r'\s*=.*$'
            config = re.sub(pattern, lambda _: key + ' = ' + value, config) if re.search(pattern, config) else key + ' = ' + value + '\n' + config
        digest = hashlib.sha256((account['image'] + '\n' + config).encode()).hexdigest()
        manifest = path / 'profile.json'
        current = state(account)
        name = account['container'] + '-tools'
        status = manager.inspect(name)
        rebuild = (current.get('digest') != digest or status['status'] == 'missing' or
                   type(current.get('port')) is not int or not 0 < current['port'] < 65536)
        cold = rebuild or not status['running'] or not current.get('ready', True)
        if rebuild:
            trim(manager, account)
            remove(manager, account)
            private_write(path / 'clewdr.toml', config)
            try:
                manager.run_worker(account, directory=str(path), port=0, name=name)
                current = {'digest': digest, 'port': published_port(manager, name), 'used_at': time.time(), 'ready': False}
                private_write(manifest, json.dumps(current))
                manager.wait_ready(current['port'])
            except Exception:
                remove(manager, account)
                raise
        elif cold:
            private_write(manifest, json.dumps({**current, 'ready': False}))
            try:
                if not status['running']:
                    manager.docker('start', name)
                manager.wait_ready(current['port'])
            except Exception:
                remove(manager, account)
                raise
        private_write(manifest, json.dumps({**current, 'used_at': time.time(), 'ready': True}))
        publish(manager, account, True)
        elapsed = round(time.monotonic() - began, 6)
        if cold or purpose != 'prewarm':
            print(json.dumps({'event': 'worker_profile_access', 'account_id': account['id'],
                              'purpose': purpose, 'cache_hit': not cold, 'seconds': elapsed}), flush=True)
        from request_timing import CURRENT
        timing = getattr(CURRENT, 'timing', None)
        if timing:
            timing.profiles.append({'account_id': account['id'], 'cache_hit': not cold, 'seconds': elapsed})
        return current['port']


def stop(manager, account):
    with locked(directory(account) / 'lifecycle.lock'):
        remove(manager, account)


def remove(manager, account):
    publish(manager, account, False)
    name = account['container'] + '-tools'
    if manager.inspect(name)['status'] != 'missing':
        manager.docker('stop', name)
        manager.docker('rm', name)
    (Path(account['directory']) / 'profiles' / PROFILE / 'profile.json').unlink(missing_ok=True)


def maintain(manager, stopped=None, blocking=False):
    if not manager.worker_profiles_enabled:
        return True
    with locked(manager.data / 'worker-prewarm.lock', blocking=blocking) as acquired:
        if not acquired:
            return False
        if manager.cluster:
            manager.cluster.reap_prewarm()
        sync_sessions(manager)
        manager.refresh()
        accounts = [a for a in local_accounts(manager) if a['status'] == 'ready' and
                    max(a.get('cooldown_until', 0), a.get('tools_unavailable_until', 0)) <= time.time() and not manager.expired(a)]
        accounts.sort(key=lambda a: (not warm(a), -manager.last_used.get(a['id'], 0)))
        background = getattr(manager.background, 'enabled', False)
        manager.background.enabled = True
        try:
            for item in accounts[:manager.worker_profile_warm]:
                if stopped and stopped.is_set():
                    break
                if not warm(item) and not headroom(starting=True):
                    break
                account = None
                try:
                    account = manager.acquire(item['id'], request_deadline=time.monotonic() + .05,
                                              record_dispatch=False)
                    endpoint(manager, account, PROFILE, purpose='prewarm')
                except Exception as error:
                    from manager import Problem
                    if account or not isinstance(error, Problem) or error.status not in (429, 503):
                        print('worker_prewarm_failed', item['id'], type(error).__name__, flush=True)
                finally:
                    if account:
                        manager.release(account)
        finally:
            manager.background.enabled = background
        trim(manager)
        expire(manager)
        return True


def readiness(manager):
    accounts = [a for a in local_accounts(manager) if a['status'] == 'ready' and
                max(a.get('cooldown_until', 0), a.get('tools_unavailable_until', 0)) <= time.time() and not manager.expired(a)]
    requested = min(manager.worker_profile_warm, len(accounts)) if manager.worker_profiles_enabled else 0
    target = requested if headroom() else min(2, requested)
    count = sum(warm(a) for a in accounts)
    enabled = manager.worker_profiles_enabled and manager.worker_profile_warm > 0
    return {'ok': bool(getattr(manager, 'prewarm_complete', False)) and count >= target and (bool(accounts) or not enabled),
            'warm': count, 'target': target, 'configured_target': manager.worker_profile_warm,
            'eligible': len(accounts), 'memory_limited': target < requested}


def initialize(manager):
    began = time.monotonic()
    manager.prewarm_complete = False
    def prepare():
        try:
            manager.prewarm_complete = maintain(manager, blocking=True)
        except Exception as error:
            print('worker_prewarm_startup_failed', type(error).__name__, flush=True)
    worker = threading.Thread(target=prepare, daemon=True)
    worker.start()
    worker.join(STARTUP_SECONDS)
    print(json.dumps({'event': 'worker_prewarm_startup', 'seconds': round(time.monotonic() - began, 6),
                      'pending': worker.is_alive(), **readiness(manager)}), flush=True)
    return worker


def run(manager, stopped):
    while not stopped.wait(15):
        try:
            if maintain(manager, stopped):
                manager.prewarm_complete = True
        except Exception as error:
            print('worker_prewarm_maintenance_failed', type(error).__name__, flush=True)
