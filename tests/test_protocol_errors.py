import json
import unittest
from unittest.mock import patch
import test_manager as base
from protocol_errors import wrap, retry_after


class ProtocolErrorTests(unittest.TestCase):
    setUp = base.ManagerTests.setUp
    tearDown = base.ManagerTests.tearDown
    request = base.ManagerTests.request
    payload = base.ManagerTests.payload
    assert_idle = base.ManagerTests.assert_idle

    def test_retry_after_values(self):
        for value in ('0', '1.5', '120', 'Wed, 21 Oct 2026 07:28:00 GMT'):
            self.assertEqual(retry_after(value), value)
        for value in (None, '', '-1', 'NaN', 'Infinity', '1_000', '1e3', 'later', '1\r\nX-Injected: yes'):
            self.assertIsNone(retry_after(value))

    def test_upstream_retry_after_reaches_client_and_does_not_leak_to_success(self):
        from test_web_gateway import raw_output
        original = base.Worker.send_header
        def header(worker, name, value):
            original(worker, name, value)
            if name == 'Content-Type':original(worker, 'Retry-After', '2')
        first = self.manager.get_account(self.identity)
        for tools in (False, True):
            if tools:
                self.manager.web_tools_enabled = True
                from test_web_gateway import TOOLS
                payload = {**self.payload(), 'max_tokens': 1024, 'tools': TOOLS}
            else:payload = self.payload()
            base.Worker.replies['Bearer ' + first['key']] = (503, b'{"error":"fixture"}')
            with patch.object(base.Worker, 'send_header', header):
                status, headers, _ = self.request('POST', '/v1/messages', payload)
            self.assertEqual(status, 503)
            self.assertEqual(headers.get('Retry-After'), '2')
            first.update(consecutive_failures=0, status='ready')
            self.manager.save()
            second = self.manager.add({'name': 'retry-second-' + str(tools),
                'sessionKey': 'sk-ant-sid02-' + ('B' if tools else 'C') * 100 + '-ABCDEF' + 'AA',
                'proxy': '127.0.0.1:1080:u:p'})
            base.Worker.replies['Bearer ' + self.manager.get_account(second)['key']] = (
                200, raw_output() if tools else b'{"content":[{"type":"text","text":"OK"}],"stop_reason":"end_turn","model":"claude-sonnet-4-6","usage":{"input_tokens":1,"output_tokens":1}}')
            self.manager.last_used[second] = self.manager.cursor + 100
            dispatched = len(base.Worker.seen)
            with patch.object(base.Worker, 'send_header', header):
                status, headers, _ = self.request('POST', '/v1/messages', payload)
            self.assertEqual(status, 200)
            self.assertEqual(len(base.Worker.seen) - dispatched, 2)
            self.assertNotIn('Retry-After', headers)
            self.assert_idle()
            self.manager.set_status(self.manager.get_account(second), 'disabled', 'test completed')
            first.update(consecutive_failures=0, status='ready')
            self.manager.save()

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
