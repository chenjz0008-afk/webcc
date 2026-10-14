"""Read-only network tools with private Brave credentials and explicit proxy egress."""
import html
import os
import re
import threading
import time
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from search_domains import SearchDomains
from source_text import text
from web_files import FileProblem

SLOTS = threading.BoundedSemaphore(4)
ENDPOINT = 'https://api.search.brave.com/res/v1/web/search'
RATE = '''
local t=redis.call('TIME')
local now=tonumber(t[1])*1000+math.floor(tonumber(t[2])/1000)
local next=tonumber(redis.call('GET',KEYS[1]) or '0')
if next > now then return next-now end
redis.call('SET',KEYS[1],now+tonumber(ARGV[1]),'PX',tonumber(ARGV[1])+1000)
return 0
'''
RATE_LOCK = threading.Lock()
NEXT = 0


def enabled():
    return bool(os.environ.get('BRAVE_SEARCH_API_KEY_FILE') or os.environ.get('BRAVE_SEARCH_API_KEY'))


def key():
    path = os.environ.get('BRAVE_SEARCH_API_KEY_FILE')
    try:
        value = Path(path).read_text().strip() if path else os.environ.get('BRAVE_SEARCH_API_KEY', '')
    except OSError:
        raise FileProblem(503, 'Search credential file is unavailable') from None
    if not value:
        raise FileProblem(503, 'Search credential is empty')
    return value


def proxy():
    value = os.environ.get('MANAGER_TOOLS_PROXY') or os.environ.get('MANAGER_SOURCE_PROXY') or os.environ.get('MANAGER_E2B_PROXY', '')
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https', 'socks5', 'socks5h'} or not parsed.hostname or not parsed.port:
        raise FileProblem(503, 'Network tools require an explicit outbound proxy')
    return value


def search(manager, owner, fields):
    if not enabled():
        raise FileProblem(503, 'Brave Search is not configured')
    query, count = fields.get('query'), fields.get('count', 5)
    if not isinstance(query, str) or not query.strip() or len(query) > 600 or len(query.split()) > 75:
        raise FileProblem(400, 'Search query requires 1 to 600 characters and at most 75 words')
    if type(count) is not int or not 1 <= count <= 20:
        raise FileProblem(400, 'Search count must be between one and 20')
    policy = SearchDomains(fields.get('allowed_domains') or [], fields.get('blocked_domains') or [], query)
    constraints = []
    for host, path in [*policy.allowed, *policy.blocked]:
        if re.search(r'[\s()"]', path):
            raise FileProblem(400, 'Search domain paths cannot contain query operators')
    if policy.allowed:
        constraints.append('(' + ' OR '.join('site:' + h + p for h, p in policy.allowed) + ')')
    constraints += ['-site:' + h + p for h, p in policy.blocked]
    actual = ' '.join([query.strip(), *constraints])
    if len(actual) > 600 or len(actual.split()) > 75:
        raise FileProblem(400, 'Query and source constraints exceed Brave query limits')
    params = {'q': actual, 'count': count, 'extra_snippets': 'true', 'text_decorations': 'false', 'result_filter': 'web'}
    if fields.get('freshness'):
        value = fields['freshness']
        if not isinstance(value, str) or not re.fullmatch(r'p[dwmy]|\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2}', value):
            raise FileProblem(400, 'Invalid search freshness')
        params['freshness'] = value
    return manager.processing_cache.memo(owner, 'brave-search', params, lambda: request(params, policy, manager, owner), ttl=60)


def pace(manager, owner):
    global NEXT
    interval = 1 / max(.1, min(50, float(os.environ.get('MANAGER_BRAVE_RPS', '1'))))
    deadline = time.monotonic() + 10
    while True:
        if owner != 'platform':
            manager.api_keys.check_active(owner)
        try:
            if manager.shared_limits:
                identity = hashlib.sha256(key().encode()).hexdigest()[:16]
                wait = manager.shared_limits.client.eval(RATE, 1, 'webcc:brave:rate:' + identity, int(interval * 1000)) / 1000
            else:
                with RATE_LOCK:
                    now = time.monotonic()
                    wait = max(0, NEXT - now)
                    if not wait:
                        NEXT = now + interval
        except Exception:
            raise FileProblem(503, 'Search rate coordination unavailable') from None
        if not wait:
            return
        if time.monotonic() + wait >= deadline:
            raise FileProblem(429, 'Search rate queue deadline exceeded')
        time.sleep(min(wait, .2))


def request(params, policy, manager, owner):
    if not SLOTS.acquire(blocking=False):
        raise FileProblem(429, 'Search service is busy')
    try:
        pace(manager, owner)
        started = time.monotonic()
        with httpx.Client(proxy=proxy(), trust_env=False, timeout=12, follow_redirects=False) as client:
            with client.stream('GET', ENDPOINT, params=params, headers={
                    'Accept': 'application/json', 'X-Subscription-Token': key()}) as response:
                if response.status_code != 200:
                    problem = FileProblem(429 if response.status_code == 429 else 503, 'Brave Search unavailable')
                    problem.retry_after = response.headers.get('retry-after')
                    raise problem
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > 2097152:
                        raise FileProblem(502, 'Search response exceeds 2 MiB')
        import json
        data = json.loads(raw)
        results = []
        for item in (data.get('web') or {}).get('results') or []:
            url = item.get('url')
            if not policy.accepts(url):
                continue
            snippets = [item.get('description', ''), *(item.get('extra_snippets') or [])]
            results.append({'url': url, 'title': html.unescape(item.get('title', '')),
                            'snippets': [html.unescape(s)[:2000] for s in snippets if isinstance(s, str) and s],
                            'age': item.get('age'), 'provider': 'brave'})
        print(json.dumps({'event': 'brave_search_completed', 'seconds': round(time.monotonic() - started, 3), 'results': len(results)}), flush=True)
        return {'provider': 'brave', 'results': results, 'query': params['q']}
    except FileProblem:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise FileProblem(502, 'Search transport or response failed; direct fallback is disabled') from None
    finally:
        SLOTS.release()


def route(handler, path):
    handler.authenticate(scope='messages')
    from web_tools.history import load_json
    fields = load_json(handler.body(16384))
    if not isinstance(fields, dict):
        raise FileProblem(400, 'Network request must be an object')
    owner = handler.caller_key or 'platform'
    if path == '/v1/network/search':
        result = search(handler.manager, owner, fields)
    else:
        url = fields.get('url')
        if not isinstance(url, str):
            raise FileProblem(400, 'Fetch requires a URL')
        result = text(handler.manager, owner, url)
    handler.check_caller()
    handler.respond(200, result)
