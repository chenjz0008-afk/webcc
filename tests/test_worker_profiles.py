import json
import copy
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import test_manager as base
from worker_profiles import PROFILE, endpoint, expire, maintain, published_port, ready_accounts, state, warm, initialize, readiness, sync_sessions


class Profiles(unittest.TestCase):
    def setUp(self):
        base.ManagerTests.setUp(self)
        memory = patch('worker_profiles.headroom', return_value=True)
        memory.start(); self.addCleanup(memory.stop)
    tearDown = base.ManagerTests.tearDown

    def accounts(self, count):
        self.manager.worker_profiles_enabled = True
        result = [self.manager.get_account(self.identity)]
        for n in range(count - 1):
            identity = self.manager.add({'name': 'warm-' + str(n),
                'sessionKey': 'sk-ant-sid02-' + chr(66 + n) * 100 + '-ABCDEFAA', 'proxy': '127.0.0.1:1080'})
            result.append(self.manager.get_account(identity))
        return result

    def test_retired_session_skipped_before_dispatch_without_quarantine_or_reset(self):
        accounts = self.accounts(2)
        for account in accounts:
            endpoint(self.manager, account, PROFILE)
        path = Path(accounts[0]['directory']) / 'profiles' / PROFILE / 'clewdr.toml'
        import tomllib
        cookie = tomllib.loads(path.read_text())['cookie_array'][0]['cookie']
        original = '[[wasted_cookie]]\ncookie = ' + json.dumps(cookie) + '\nreason = "Null"\n'
        path.write_text(original)
        selected = self.manager.acquire(profile=PROFILE)
        try:
            self.assertEqual(selected['id'], accounts[1]['id'])
            self.assertGreater(accounts[0]['tools_unavailable_until'], time.time())
            self.assertEqual(accounts[0]['status'], 'ready')
            self.assertEqual(accounts[0].get('consecutive_failures', 0), 0)
            self.assertEqual(path.read_text(), original)
            self.assertNotIn(accounts[0]['id'], self.manager.dispatches)
        finally:
            self.manager.release(selected)
        plain = self.manager.acquire(accounts[0]['id'])
        self.manager.release(plain)

    def test_cooling_session_uses_reset_boundary_and_only_upstream_recovery_clears_it(self):
        account = self.accounts(1)[0]
        endpoint(self.manager, account, PROFILE)
        path = Path(account['directory']) / 'profiles' / PROFILE / 'clewdr.toml'
        original = path.read_text()
        reset = int(time.time()) + 3600
        path.write_text(original + '\nreset_time = ' + str(reset) + '\n')
        sync_sessions(self.manager)
        self.assertEqual(account['tools_unavailable_until'], reset + 300)
        with self.assertRaises(base.Problem):
            self.manager.acquire(profile=PROFILE)
        self.assertEqual(self.manager.total, 0)
        path.write_text(original)
        sync_sessions(self.manager)
        self.assertEqual(account['tools_unavailable_until'], 0)
        account['status'] = 'quarantined'
        account['tools_unavailable_until'] = reset
        self.manager.save()
        sync_sessions(self.manager)
        self.assertEqual(account['status'], 'quarantined')
        self.assertEqual(account['tools_unavailable_until'], reset)

    def test_old_session_files_and_broken_state_do_not_disable_new_credentials(self):
        account = self.accounts(1)[0]
        endpoint(self.manager, account, PROFILE)
        path = Path(account['directory']) / 'profiles' / PROFILE / 'clewdr.toml'
        path.write_text('[[wasted_cookie]]\ncookie = "old-credential"\nreason = "Null"\n')
        sync_sessions(self.manager)
        self.assertEqual(account.get('tools_unavailable_until', 0), 0)
        path.write_text('broken = [')
        sync_sessions(self.manager)
        self.assertEqual(account.get('tools_unavailable_until', 0), 0)

    def test_session_health_refreshes_and_retries_a_shared_state_conflict(self):
        from cluster_state import StateConflict
        account = self.accounts(1)[0]
        endpoint(self.manager, account, PROFILE)
        path = Path(account['directory']) / 'profiles' / PROFILE / 'clewdr.toml'
        import tomllib
        cookie = tomllib.loads(path.read_text())['cookie_array'][0]['cookie']
        path.write_text('[[wasted_cookie]]\ncookie = ' + json.dumps(cookie) + '\nreason = "Null"\n')
        persisted, writes = copy.deepcopy(self.manager.state), []
        def refresh():
            self.manager.state = copy.deepcopy(persisted)
        def save():
            writes.append(True)
            if len(writes) == 1:
                raise StateConflict('Concurrent health update')
            persisted.update(copy.deepcopy(self.manager.state))
        with patch.object(self.manager, 'cluster', True), patch.object(self.manager, 'refresh', side_effect=refresh), patch.object(self.manager, 'save', side_effect=save):
            sync_sessions(self.manager)
        self.assertEqual(len(writes), 2)
        self.assertGreater(persisted['accounts'][account['id']]['tools_unavailable_until'], time.time())

    def test_other_account_and_warm_reuse_do_not_wait_for_cold_readiness(self):
        accounts = self.accounts(3)
        endpoint(self.manager, accounts[0], PROFILE)
        entered, release = threading.Event(), threading.Event()
        original = self.manager.wait_ready
        def wait(port):
            if threading.current_thread().name == 'cold':
                entered.set()
                if not release.wait(3):
                    raise TimeoutError('fixture readiness timed out')
            return original(port)
        with patch.object(self.manager, 'wait_ready', side_effect=wait):
            with ThreadPoolExecutor(3, thread_name_prefix='profiles') as pool:
                def cold():
                    threading.current_thread().name = 'cold'
                    return endpoint(self.manager, accounts[1], PROFILE)
                pending = pool.submit(cold)
                try:
                    self.assertTrue(entered.wait(1))
                    self.assertEqual(pool.submit(endpoint, self.manager, accounts[0], PROFILE).result(1), accounts[0]['port'])
                    self.assertEqual(pool.submit(endpoint, self.manager, accounts[2], PROFILE).result(1), accounts[2]['port'])
                    expire(self.manager)
                    self.assertIn(accounts[1]['container'] + '-tools', self.manager.containers)
                finally:
                    release.set()
                pending.result(1)

    def test_same_account_is_created_once_and_uses_atomic_docker_port(self):
        account = self.accounts(1)[0]
        with patch.object(self.manager, 'run_worker', wraps=self.manager.run_worker) as run:
            with patch.object(self.manager, 'free_port', side_effect=AssertionError('Must let Docker allocate')):
                with ThreadPoolExecutor(4) as pool:
                    ports = list(pool.map(lambda _: endpoint(self.manager, account, PROFILE), range(4)))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.kwargs['port'], 0)
            self.assertEqual(ports, [account['port']] * 4)

    def test_invalid_port_or_readiness_failure_cleans_profile(self):
        account = self.accounts(1)[0]
        with patch.object(self.manager, 'wait_ready', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                endpoint(self.manager, account, PROFILE)
        self.assertEqual(state(account), {})
        self.assertNotIn(account['container'] + '-tools', self.manager.containers)
        endpoint(self.manager, account, PROFILE)
        container = self.manager.containers[account['container'] + '-tools']
        container['NetworkSettings']['Ports']['8484/tcp'][0]['HostIp'] = '0.0.0.0'
        with self.assertRaises(base.Problem):
            published_port(self.manager, account['container'] + '-tools')

    def test_maintenance_retains_target_and_never_inflates_business_dispatches(self):
        accounts = self.accounts(5)
        self.manager.worker_profile_warm = 4
        self.manager.set_status(accounts[-1], 'quarantined', 'fixture')
        maintain(self.manager)
        self.assertEqual(sum(warm(a) for a in accounts), 4)
        self.assertFalse(warm(accounts[-1]))
        self.assertEqual(self.manager.dispatches, {})
        self.assertEqual(self.manager.last_used, {})
        self.assertEqual(self.manager.total, 0)
        with patch.object(self.manager, 'run_worker', wraps=self.manager.run_worker) as run:
            maintain(self.manager)
            run.assert_not_called()
        self.manager.worker_profile_warm = 0
        with patch.object(self.manager, 'acquire', wraps=self.manager.acquire) as acquire:
            maintain(self.manager)
            acquire.assert_not_called()

    def test_startup_readiness_and_memory_degradation(self):
        self.accounts(4)
        self.manager.worker_profile_warm = 4
        self.assertFalse(readiness(self.manager)['ok'])
        initialize(self.manager)
        self.assertEqual(readiness(self.manager)['warm'], 4)
        self.assertTrue(readiness(self.manager)['ok'])
        self.assertEqual(self.manager.total, 0)
        with patch('worker_profiles.headroom', return_value=False):
            maintain(self.manager)
            result = readiness(self.manager)
        self.assertTrue(result['ok'])
        self.assertTrue(result['memory_limited'])
        self.assertEqual(result['target'], 2)

    def test_http_listener_starts_after_initialization(self):
        from manager import main
        order = []
        with patch('manager.Manager', return_value=self.manager), patch('manager.threading.Thread'), \
                patch('worker_profiles.initialize', side_effect=lambda m: order.append('prewarm')), patch('manager.Server') as server:
            server.return_value.serve_forever.side_effect = lambda: order.append('listen')
            main()
        self.assertEqual(order, ['prewarm', 'listen'])

    def test_failed_initialization_does_not_claim_readiness(self):
        self.accounts(1)
        with patch('worker_profiles.maintain', side_effect=OSError('fixture Docker failure')):
            initialize(self.manager)
        self.assertFalse(self.manager.prewarm_complete)
        self.assertFalse(readiness(self.manager)['ok'])

    def test_readiness_is_separate_from_liveness(self):
        self.accounts(1)
        status, _, _ = base.ManagerTests.request(self, 'GET', '/healthz')
        self.assertEqual(status, 200)
        self.assertEqual(base.ManagerTests.request(self, 'GET', '/readyz')[0], 503)
        initialize(self.manager)
        self.assertEqual(base.ManagerTests.request(self, 'GET', '/readyz')[0], 200)

    def test_no_eligible_local_resource_is_not_a_ready_warm_pool(self):
        account = self.accounts(1)[0]
        self.manager.set_status(account, 'quarantined', 'fixture')
        initialize(self.manager)
        self.assertEqual(readiness(self.manager)['eligible'], 0)
        self.assertFalse(readiness(self.manager)['ok'])

    def test_slow_startup_is_bounded_and_later_becomes_ready(self):
        self.accounts(1)
        release = threading.Event()
        original = maintain
        def delayed(*args, **kwargs):
            release.wait(2)
            return original(*args, **kwargs)
        with patch('worker_profiles.STARTUP_SECONDS', .01), patch('worker_profiles.maintain', side_effect=delayed):
            worker = initialize(self.manager)
            try:
                self.assertTrue(worker.is_alive())
                self.assertFalse(readiness(self.manager)['ok'])
            finally:
                release.set()
                worker.join(2)
        self.assertTrue(readiness(self.manager)['ok'])

    def test_maintenance_never_takes_last_interactive_slot(self):
        accounts = self.accounts(4)
        active = [self.manager.acquire(a['id']) for a in accounts[:3]]
        try:
            maintain(self.manager)
            self.assertEqual(self.manager.total, 3)
            self.assertFalse(any(warm(a) for a in accounts))
        finally:
            for a in active:
                self.manager.release(a)

    def test_memory_pressure_stops_new_prewarming_and_keeps_busy_profiles(self):
        accounts = self.accounts(4)
        self.manager.worker_profile_warm = 4
        maintain(self.manager)
        busy = self.manager.acquire(accounts[0]['id'])
        try:
            with patch('worker_profiles.headroom', return_value=False):
                maintain(self.manager)
            self.assertIn(busy['container'] + '-tools', self.manager.containers)
            self.assertEqual(sum(warm(a) for a in accounts), 2)
            with patch('worker_profiles.headroom', return_value=False), patch.object(self.manager, 'run_worker') as run:
                maintain(self.manager)
                run.assert_not_called()
        finally:
            self.manager.release(busy)

    def test_config_update_rebuilds_and_old_manifest_recovers(self):
        account = self.accounts(1)[0]
        endpoint(self.manager, account, PROFILE)
        path = Path(account['directory']) / 'profiles/client-tools/profile.json'
        old = state(account); old.pop('ready'); path.write_text(json.dumps(old))
        with patch.object(self.manager, 'run_worker', wraps=self.manager.run_worker) as run:
            endpoint(self.manager, account, PROFILE)
            run.assert_not_called()
        source = Path(account['directory']) / 'clewdr.toml'
        source.write_text(source.read_text().replace('preserve_chats = false', 'preserve_chats = true'))
        with patch.object(self.manager, 'run_worker', wraps=self.manager.run_worker) as run:
            endpoint(self.manager, account, PROFILE)
            self.assertEqual(run.call_count, 1)
        self.assertNotEqual(old['digest'], state(account)['digest'])
        broken = state(account); broken['port'] = 0; path.write_text(json.dumps(broken))
        self.assertEqual(endpoint(self.manager, account, PROFILE), account['port'])

    def test_remote_warm_hints_are_batched_and_never_replace_authoritative_checks(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        remote = [{'id': 'a', 'node_id': 'node-2'}, {'id': 'b', 'node_id': 'node-3'}]
        client = Mock(); client.mget.return_value = [b'1', None]
        manager = SimpleNamespace(node_id='node-1', shared_limits=SimpleNamespace(client=client))
        self.assertEqual(ready_accounts(manager, remote), {'a'})
        client.mget.assert_called_once_with(['webcc:worker-profile:node-2:a', 'webcc:worker-profile:node-3:b'])
        client.mget.side_effect = OSError('fixture')
        self.assertEqual(ready_accounts(manager, remote), set())


if __name__ == '__main__':
    unittest.main()
