import concurrent.futures
import json
import threading
import time
import unittest
from unittest.mock import patch
import test_manager as base
from api_keys import ApiKeys, KeyProblem


class ApiKeyTests(unittest.TestCase):
    setUp = base.ManagerTests.setUp
    tearDown = base.ManagerTests.tearDown
    request = base.ManagerTests.request
    payload = base.ManagerTests.payload
    assert_idle = base.ManagerTests.assert_idle

    def create(self, **fields):
        return self.manager.api_keys.create({'name': 'caller', 'accounts': [self.identity], **fields})

    def test_admin_creation_listing_and_no_plaintext_persistence(self):
        status, _, raw = self.request('POST', '/admin/api-keys', {'name': 'A', 'accounts': [self.identity]}, key='admin-password')
        self.assertEqual(status, 201)
        credential = json.loads(raw)
        status, _, listed = self.request('GET', '/admin/api-keys', key='admin-password')
        self.assertEqual(status, 200)
        self.assertNotIn(credential['key'].encode(), listed)
        self.assertNotIn('hash', json.loads(listed)['keys'][0])
        self.assertNotIn(credential['key'], self.manager.path.read_text())
        self.assertEqual(self.request('GET', '/admin/api-keys', key=credential['key'])[0], 401)

    def test_restart_preserves_authentication_and_revocation(self):
        key = self.create()
        restored = base.FakeManager(self.temp.name, 'image', 'api-password', 'admin-password')
        self.assertEqual(restored.api_keys.authenticate(key['key'], 'messages')[0], key['id'])
        restored.api_keys.revoke(key['id'])
        with self.assertRaises(KeyProblem):
            restored.api_keys.authenticate(key['key'], 'messages')

    def test_revoked_or_expired_key_rejected_without_dispatch(self):
        key = self.create(expires_at=int(time.time()) + 100)
        self.manager.api_keys.revoke(key['id'])
        self.assertEqual(self.request('POST', '/v1/chat/completions', self.payload(), key=key['key'])[0], 401)
        expired = self.create(expires_at=int(time.time()) + 100)
        self.manager.api_keys.records[expired['id']]['expires_at'] = int(time.time()) - 1
        self.assertEqual(self.request('GET', '/v1/models', key=expired['key'])[0], 401)
        self.assertEqual(base.Worker.seen, [])

    def test_scope_and_rate_errors_are_claude_shaped(self):
        key = self.create(scopes=['models'], rpm=1)
        status, _, raw = self.request('POST', '/v1/messages', self.payload(), key=key['key'])
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(raw)['error']['type'], 'permission_error')
        self.assertEqual(self.request('GET', '/v1/models', key=key['key'])[0], 200)
        status, headers, raw = self.request('GET', '/v1/models', key=key['key'])
        self.assertEqual(status, 429)
        self.assertTrue(1 <= int(headers['Retry-After']) <= 61)
        self.assertEqual(json.loads(raw)['error']['type'], 'rate_limit_error')

    def test_two_callers_are_routed_to_their_assigned_accounts(self):
        second = self.manager.add({'name': 'B', 'sessionKey': 'sk-ant-sid02-' + 'B' * 100 + '-ABCDEF' + 'AA', 'proxy': '127.0.0.1:1080:u:p'})
        first_key = self.create()
        second_key = self.create(accounts=[second])
        for key, identity in [(first_key, self.identity), (second_key, second)] * 2:
            status, _, _ = self.request('POST', '/v1/chat/completions', self.payload(), key=key['key'])
            self.assertEqual(status, 200)
            self.assert_idle()
            headers = base.Worker.seen[-1]['headers']
            self.assertEqual(headers['Authorization'], 'Bearer ' + self.manager.get_account(identity)['key'])
            self.assertNotIn(key['key'], json.dumps(headers))

    def test_failover_cannot_escape_assigned_pool(self):
        self.manager.add({'name': 'B', 'sessionKey': 'sk-ant-sid02-' + 'B' * 100 + '-ABCDEF' + 'AA', 'proxy': '127.0.0.1:1080:u:p'})
        key = self.create()
        base.Worker.replies['Bearer ' + self.manager.get_account(self.identity)['key']] = (500, b'{"error":"secret"}')
        self.assertEqual(self.request('POST', '/v1/messages', self.payload(), key=key['key'])[0], 500)
        self.assertEqual(len(base.Worker.seen), 1)
        self.assert_idle()

    def test_experimental_scope_and_pool_restriction(self):
        from test_web_gateway import raw_output, TOOLS
        self.manager.web_tools_enabled = True
        payload = {'model': 'webcc-prompt-v1', 'tools': TOOLS, 'max_tokens': 128,
                   'messages': [{'role': 'user', 'content': 'Read A'}]}
        key = self.create()
        self.assertEqual(self.request('POST', '/v1/messages', payload, key=key['key'], extra={'X-WebCC-Tools': 'prompt-v1'})[0], 403)
        allowed = self.create(scopes=['messages', 'experimental_tools'])
        base.Worker.replies['Bearer ' + self.manager.get_account(self.identity)['key']] = (200, raw_output())
        self.assertEqual(self.request('POST', '/v1/messages', payload, key=allowed['key'], extra={'X-WebCC-Tools': 'prompt-v1'})[0], 200)
        self.assert_idle()

    def test_atomic_rate_limit_and_window_expiry(self):
        key = self.create(rpm=1)
        def authenticate(_):
            try:self.manager.api_keys.authenticate(key['key'], 'messages');return 200
            except KeyProblem as error:return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(authenticate, range(8)))
        self.assertEqual(results.count(200), 1)
        self.assertEqual(results.count(429), 7)
        with patch('api_keys.time.monotonic', return_value=time.monotonic() + 61):
            self.manager.api_keys.authenticate(key['key'], 'messages')

    def test_queued_request_revoked_before_selection(self):
        self.manager.queue_seconds = 5
        key = self.create()
        held = self.manager.acquire()
        results = []
        thread = threading.Thread(target=lambda: results.append(self.request('POST', '/v1/chat/completions', self.payload(), key=key['key'])[0]))
        thread.start()
        with self.manager.condition:
            self.assertTrue(self.manager.condition.wait_for(lambda: self.manager.waiting == 1, timeout=2))
        self.manager.api_keys.revoke(key['id'])
        thread.join(timeout=3)
        self.assertEqual(results, [401])
        self.assertEqual(base.Worker.seen, [])
        self.assertEqual(self.manager.total, 1)
        self.manager.release(held)
        self.assert_idle()

    def test_invalid_configuration_and_master_key_compatibility(self):
        for fields in [{'accounts': []}, {'accounts': ['missing']}, {'scopes': ['admin']}, {'rpm': True}, {'expires_at': 1}, {'name': ''}]:
            with self.assertRaises(KeyProblem):self.create(**fields)
        self.assertEqual(self.request('GET', '/v1/models')[0], 200)
