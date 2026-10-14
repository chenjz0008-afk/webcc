import json
import unittest
from unittest.mock import patch
import test_manager as base
from client_protocol import catalogue
from test_client_compatibility import stream


class PublicClients(unittest.TestCase):
    setUp = base.ManagerTests.setUp
    tearDown = base.ManagerTests.tearDown
    request = base.ManagerTests.request

    def test_chatbox_catalogue_preserves_openai_fields(self):
        status, _, raw = self.request('GET', '/v1/models?limit=990', extra={'anthropic-version': '2023-06-01'})
        self.assertEqual(status, 200)
        value = json.loads(raw)
        self.assertEqual(value['data'][0]['type'], 'model')
        self.assertEqual(value['data'][0]['display_name'], 'claude-sonnet-4-6')
        self.assertEqual(value['data'][0]['created_at'], '1970-01-01T00:00:00+00:00')
        from anthropic.types import ModelInfo
        self.assertEqual(ModelInfo.model_validate(value['data'][0]).lifecycle, 'active')
        original = {'object': 'list', 'data': [{'id': 'fixture', 'object': 'model', 'created': 0}]}
        self.assertEqual(catalogue(original)['data'][0]['object'], 'model')
        self.assertEqual(original['data'][0], {'id': 'fixture', 'object': 'model', 'created': 0})
        self.assertIsNone(catalogue({'data': []})['first_id'])

    def test_browser_preflight_never_calls_worker_or_authenticates(self):
        status, headers, _ = self.request('OPTIONS', '/v1/messages', key='', extra={
            'Origin': 'https://web.chatboxai.app', 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'Content-Type,X-Api-Key,Anthropic-Version,Anthropic-Dangerous-Direct-Browser-Access'})
        self.assertEqual(status, 204)
        self.assertEqual(headers['Access-Control-Allow-Origin'], '*')
        self.assertNotIn('Access-Control-Allow-Credentials', headers)
        self.assertEqual(base.Worker.seen, [])
        status, headers, _ = self.request('POST', '/v1/messages', {}, key='', extra={'Origin': 'https://web.chatboxai.app'})
        self.assertEqual(status, 401)
        self.assertEqual(headers['Access-Control-Allow-Origin'], '*')
        self.assertEqual(base.Worker.seen, [])

    def test_admin_and_unsupported_preflight_remain_closed(self):
        for path, method, headers, expected in [('/admin/accounts', 'GET', '', 404),
                ('https://evil.example/v1/messages', 'POST', '', 404),
                ('/v1/messages', 'PATCH', '', 400), ('/v1/messages', 'POST', 'X-Unknown', 400)]:
            status, received, _ = self.request('OPTIONS', path, key='', extra={'Origin': 'https://example.com',
                'Access-Control-Request-Method': method, 'Access-Control-Request-Headers': headers})
            self.assertEqual(status, expected)
            if path.startswith('/admin/') or path.startswith('https:'):
                self.assertNotIn('Access-Control-Allow-Origin', received)

    def test_empty_stream_diagnostics_exclude_contents_and_distinguish_cutoff(self):
        key = 'Bearer ' + self.manager.get_account(self.identity)['key']
        base.Worker.replies[key] = (200, stream([{'type': 'thinking', 'thinking': 'private-test-secret'}], 'max_tokens'), 'text/event-stream')
        fields = {'model': 'fixture', 'max_tokens': 16, 'thinking': {'type': 'disabled'}, 'messages': [{'role': 'user', 'content': 'private-user-secret'}]}
        with patch('builtins.print') as output:
            status, _, _ = self.request('POST', '/v1/messages', fields)
        self.assertEqual(status, 502)
        records = [json.loads(call.args[0]) for call in output.call_args_list if call.args and isinstance(call.args[0], str) and call.args[0].startswith('{')]
        record = next(x for x in records if x.get('event') == 'native_empty')
        self.assertEqual(record['stop_reason'], 'max_tokens')
        self.assertEqual(record['thinking_characters'], len('private-test-secret'))
        self.assertEqual(record['block_types'], ['thinking'])
        self.assertNotIn('private-test-secret', json.dumps(records))
        self.assertNotIn('private-user-secret', json.dumps(records))


if __name__ == '__main__':
    unittest.main()
