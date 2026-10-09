import json
import unittest
import test_manager as base
from protocol_errors import wrap


class ProtocolErrorTests(unittest.TestCase):
    setUp = base.ManagerTests.setUp
    tearDown = base.ManagerTests.tearDown
    request = base.ManagerTests.request
    payload = base.ManagerTests.payload
    assert_idle = base.ManagerTests.assert_idle

    def check(self, status, headers, body, kind):
        data = json.loads(body)
        self.assertEqual(data['type'], 'error')
        self.assertEqual(data['error']['type'], kind)
        self.assertEqual(data['request_id'], headers['Request-Id'])
        self.assertEqual(headers['Request-Id'], headers['X-Request-Id'])
        self.assert_idle()

    def test_authentication_error_traced_before_dispatch(self):
        status, headers, body = self.request('POST', '/v1/messages', self.payload(), key='wrong', extra={'Request-Id': 'spoof'})
        self.assertEqual(status, 401)
        self.check(status, headers, body, 'authentication_error')
        self.assertNotEqual(headers['Request-Id'], 'spoof')
        self.assertEqual(base.Worker.seen, [])

    def test_unknown_resource_and_invalid_mode(self):
        status, headers, body = self.request('POST', '/v1/messages/unknown', self.payload())
        self.assertEqual(status, 404)
        self.check(status, headers, body, 'not_found_error')
        status, headers, body = self.request('POST', '/v1/messages', self.payload(), extra={'X-WebCC-Tools': 'invalid'})
        self.assertEqual(status, 400)
        self.check(status, headers, body, 'invalid_request_error')
        self.assertEqual(base.Worker.seen, [])

    def test_request_size_rejected_before_dispatch(self):
        status, headers, body = self.request('POST', '/v1/messages', self.payload(), extra={'Content-Length': str(33 * 1024 * 1024)})
        self.assertEqual(status, 413)
        self.check(status, headers, body, 'request_too_large')
        self.assertEqual(base.Worker.seen, [])

    def test_exact_model_rejected_without_dispatch_or_quarantine(self):
        status, _, body = self.request('POST', '/v1/messages', self.payload(), extra={'X-WebCC-Model-Policy': 'exact'})
        self.assertEqual(status, 409)
        self.assertIn(b'Exact model selection', body)
        self.assertEqual(base.Worker.seen, [])
        self.assertEqual(self.manager.get_account(self.identity)['status'], 'ready')
        self.assert_idle()

    def test_upstream_failure_and_rate_limit_are_sanitized(self):
        account = self.manager.get_account(self.identity)
        for code, kind in [(500, 'api_error'), (429, 'rate_limit_error')]:
            base.Worker.replies['Bearer ' + account['key']] = (code, b'{"error":"worker-secret"}')
            status, headers, body = self.request('POST', '/v1/messages', self.payload())
            self.assertEqual(status, code)
            self.check(status, headers, body, kind)
            self.assertNotIn(b'worker-secret', body)

    def test_request_id_available_for_json_and_sse(self):
        for stream in (False, True):
            if stream:
                base.Worker.replies['Bearer ' + self.manager.get_account(self.identity)['key']] = (
                    200, b'data: {"type":"content_block_delta","delta":{"text":"OK"}}\n\ndata: {"type":"message_stop"}\n\n',
                    'text/event-stream')
            else:
                base.Worker.replies['Bearer ' + self.manager.get_account(self.identity)['key']] = (
                    200, b'{"content":[{"type":"text","text":"OK"}],"stop_reason":"end_turn"}')
            status, headers, body = self.request('POST', '/code/v1/messages', self.payload(stream))
            self.assertEqual(status, 200)
            self.assertTrue(headers['Request-Id'])
            self.assertEqual(headers['Request-Id'], headers['X-Request-Id'])
            self.assert_idle()

    def test_openai_error_body_keeps_existing_shape(self):
        status, _, body = self.request('POST', '/v1/chat/completions', self.payload(), key='wrong')
        self.assertEqual(status, 401)
        self.assertNotIn('type', json.loads(body))

    def test_extended_status_mapping_and_invalid_error_value(self):
        for code, kind in [(402, 'billing_error'), (403, 'permission_error'), (409, 'conflict_error'),
                           (502, 'api_error'), (504, 'timeout_error'), (529, 'overloaded_error')]:
            self.assertEqual(wrap(code, {'error': None}, 'id')['error']['type'], kind)
