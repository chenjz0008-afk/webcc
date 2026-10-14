import copy
import json
import os
import socket
import types
import unittest
from unittest.mock import MagicMock, patch

from processing_cache import ProcessingCache
from web_files import FileProblem
from network_tools import search, request
from search_domains import SearchDomains
from network_messages import explicit_query, messages


class NetworkTests(unittest.TestCase):
    def manager(self):
        manager = types.SimpleNamespace(admin_key='private-test-key', shared_limits=None, request_seconds=30)
        manager.processing_cache = ProcessingCache(manager)
        return manager

    def fields(self, stream=False):
        return {'model': 'test-model', 'max_tokens': 2048, 'stream': stream, 'thinking': {'type': 'disabled'},
                'system': 'You run a web search for another Claude model, which is waiting on the result.',
                'messages': [{'role': 'user', 'content': 'Perform a web search for the query: Python DictReader'}],
                'tools': [{'type': 'web_search_20250305', 'name': 'web_search', 'allowed_domains': ['docs.python.org']}]}

    def test_query_constraints_cache_and_private_owner(self):
        m = self.manager()
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': 'private-test-token'}), patch('network_tools.request', return_value={'results': []}) as work:
            fields = {'query': 'Python DictReader', 'allowed_domains': ['docs.python.org'], 'blocked_domains': ['bad.docs.python.org']}
            first = search(m, 'A', fields); first['results'].append('mutated')
            self.assertEqual(search(m, 'A', fields), {'results': []})
            search(m, 'B', fields)
            self.assertEqual(work.call_count, 2)
            params = work.call_args.args[0]
            self.assertIn('site:docs.python.org', params['q'])
            self.assertIn('-site:bad.docs.python.org', params['q'])

    def test_provider_filters_results_without_forwarding_identity(self):
        client = MagicMock(); client.__enter__.return_value = client
        response = client.stream.return_value.__enter__.return_value
        response.status_code = 200
        response.iter_bytes.return_value = iter([json.dumps({'web': {'results': [
            {'url': 'https://docs.python.org/3', 'title': 'Docs', 'description': 'Actual snippet'},
            {'url': 'https://outside.example', 'title': 'Outside'}]}}).encode()])
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY': 'private-test-token'}), patch('network_tools.proxy', return_value='socks5://proxy.example:1080'), \
                patch('network_tools.pace'), patch('network_tools.httpx.Client', return_value=client):
            result = request({'q': 'query'}, SearchDomains(['docs.python.org'], [], ''), self.manager(), 'platform')
        self.assertEqual(len(result['results']), 1)
        headers = client.stream.call_args.kwargs['headers']
        self.assertEqual(set(headers), {'Accept', 'X-Subscription-Token'})
        self.assertFalse(client.stream.call_args.kwargs.get('follow_redirects'))

    def test_missing_proxy_does_not_connect_directly(self):
        with patch.dict(os.environ, {'MANAGER_TOOLS_PROXY': '', 'MANAGER_SOURCE_PROXY': '', 'MANAGER_E2B_PROXY': ''}), \
                patch('network_tools.pace'), patch('network_tools.httpx.Client') as client:
            with self.assertRaises(FileProblem):
                request({'q': 'query'}, SearchDomains([], [], ''), self.manager(), 'platform')
            client.assert_not_called()

    def test_explicit_search_only_fast_path_is_narrow(self):
        fields = self.fields()
        self.assertEqual(explicit_query(fields, {'web_search': {}}), 'Python DictReader')
        fields['system'] = 'Ordinary user instructions'
        self.assertIsNone(explicit_query(fields, {'web_search': {}}))

    def handler(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close); self.addCleanup(right.close)
        return types.SimpleNamespace(manager=self.manager(), caller_key=None, respond=MagicMock(),
                                     connection=left, check_caller=MagicMock(), send_response=MagicMock(),
                                     send_header=MagicMock(), end_headers=MagicMock(), write_stream=MagicMock())

    def test_fast_search_emits_real_tool_results_without_model(self):
        handler = self.handler(); actual = {'provider': 'brave', 'results': [{'url': 'https://docs.python.org/3', 'title': 'Docs', 'snippets': ['Actual content']}]}
        with patch('network_messages.search', return_value=actual), patch('runtime_forward.infer') as inference:
            messages(handler, self.fields())
        result = handler.respond.call_args.args[1]
        self.assertEqual([b['type'] for b in result['content']], ['server_tool_use', 'web_search_tool_result', 'text'])
        self.assertEqual(result['model'], 'webcc-search-v1'); inference.assert_not_called()
        self.assertIn('Actual content', result['content'][-1]['text'])

    def test_normal_request_runs_actual_loop_and_honors_limit(self):
        fields = self.fields(); fields['system'] = 'Normal user'; fields['tools'][0]['max_uses'] = 1
        call = {'type': 'tool_use', 'id': 'call1', 'name': 'webcc_search', 'input': {'query': 'Python'}}
        reply = {'id': 'msg_actual', 'type': 'message', 'role': 'assistant', 'model': 'actual-model', 'content': [call],
                 'stop_reason': 'tool_use', 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        second = copy.deepcopy(reply); second['content'][0]['id'] = 'call2'
        handler = self.handler()
        with patch('runtime_forward.infer', side_effect=[reply, second]), patch('network_messages.search', return_value={'results': []}) as work:
            messages(handler, fields)
        result = handler.respond.call_args.args[1]
        self.assertEqual(work.call_count, 1)
        self.assertEqual(result['stop_reason'], 'pause_turn')
        self.assertEqual(result['content'][-1]['content']['error_code'], 'max_uses_exceeded')

    def test_provider_failure_is_not_account_failure_or_direct_fallback(self):
        handler = self.handler()
        with patch('network_messages.search', side_effect=FileProblem(503, 'Provider unavailable')):
            with self.assertRaises(FileProblem): messages(handler, self.fields())
        handler.respond.assert_not_called()

    def test_choice_none_never_calls_search(self):
        fields = self.fields(); fields['tool_choice'] = {'type': 'none'}
        with patch('web_tools.gateway.forward') as forward, patch('network_messages.search') as work:
            messages(self.handler(), fields)
        forward.assert_called_once(); work.assert_not_called()

    def test_real_stream_events_are_forwarded_before_inference_returns(self):
        fields = self.fields(stream=True); fields['system'] = 'Ordinary user'
        handler = self.handler()
        def infer(manager, owner, params, deadline, check, emit):
            emit({'type': 'message_start', 'message': {'model': 'actual-model'}})
            emit({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'Incremental answer'}})
            frames = b''.join(c.args[0] for c in handler.write_stream.call_args_list)
            self.assertIn(b'Incremental answer', frames)
            self.assertNotIn(b'message_stop', frames)
            return {'content': [{'type': 'text', 'text': 'Incremental answer'}], 'model': 'actual-model',
                    'stop_reason': 'end_turn', 'usage': {'output_tokens': 1}}
        with patch('runtime_forward.infer_stream', side_effect=infer): messages(handler, fields)
        frames = b''.join(c.args[0] for c in handler.write_stream.call_args_list)
        self.assertEqual(frames.count(b'Incremental answer'), 1)
        self.assertIn(b'message_stop', frames)

    def test_rate_limit_does_not_fall_back_or_expose_provider_body(self):
        client = MagicMock(); client.__enter__.return_value = client
        response = client.stream.return_value.__enter__.return_value
        response.status_code = 429; response.headers = {'retry-after': '3'}
        with patch('network_tools.pace'), patch('network_tools.key', return_value='private-test-token'), patch('network_tools.proxy', return_value='http://proxy:8080'), \
                patch('network_tools.httpx.Client', return_value=client):
            with self.assertRaises(FileProblem) as raised:
                request({'q': 'query'}, SearchDomains([], [], ''), self.manager(), 'platform')
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.retry_after, '3')
        self.assertEqual(client.stream.call_count, 1)
        response.iter_bytes.assert_not_called()

    def test_missing_credential_file_does_not_disable_normal_routing(self):
        from network_tools import key, enabled
        with patch.dict(os.environ, {'BRAVE_SEARCH_API_KEY_FILE': '/does-not-exist/private-brave-key'}):
            self.assertTrue(enabled())
            with self.assertRaises(FileProblem) as error: key()
        self.assertEqual(error.exception.status, 503)
        self.assertNotIn('/does-not-exist', str(error.exception))
