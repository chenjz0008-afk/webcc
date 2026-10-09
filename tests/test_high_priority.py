import asyncio
import copy
import json
import os
import types
import time
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from mcp_connector import validate_server, invoke, configured_tools, operation
from mcp_messages import prepare_request, execute, MODEL
from message_stream import events
from web_files import FileProblem

SERVER = {'type': 'url', 'name': 'deepwiki', 'url': 'https://mcp.deepwiki.com/mcp'}
TOOL = {'name': 'read', 'description': 'Read a document', 'input_schema': {'type': 'object',
        'properties': {'id': {'type': 'string'}}, 'required': ['id'], 'additionalProperties': False}}
ENV = {'MANAGER_MCP_HOSTS': 'mcp.deepwiki.com', 'MANAGER_TOOLS_PROXY': 'socks5h://user:pass@8.8.8.8:1080'}


def request():
    return {'model': MODEL, 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': 'Read A'}],
            'mcp_servers': [copy.deepcopy(SERVER)], 'tools': [{'type': 'mcp_toolset', 'mcp_server_name': 'deepwiki'}]}


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_closed_without_proxy_or_allowlist(self):
        for values in ({'MANAGER_TOOLS_PROXY': ''}, {'MANAGER_MCP_HOSTS': ''}):
            with patch.dict(os.environ, values), self.assertRaises(FileProblem):
                validate_server(SERVER)

    def test_invalid_urls_and_names(self):
        for fields in ({'url': 'http://mcp.deepwiki.com/mcp'}, {'url': 'https://mcp.deepwiki.com:invalid/mcp'},
                       {'url': 'https://u:p@mcp.deepwiki.com/mcp'}, {'url': 'https://mcp.deepwiki.com/mcp?key=x'},
                       {'url': 'https://127.0.0.1/mcp'}, {'name': None}, {'authorization_token': 'a\nb'}):
            with self.subTest(fields=fields), self.assertRaises(FileProblem):
                validate_server({**SERVER, **fields})

    def test_tool_configuration(self):
        self.assertEqual(configured_tools([TOOL], {'default_config': {'enabled': False}}), [])
        tools = configured_tools([TOOL], {'default_config': {'enabled': False}, 'configs': {'read': {'enabled': True}}})
        self.assertEqual(tools[0]['name'], 'read')
        with self.assertRaises(FileProblem):
            configured_tools([], {'default_config': {'enabled': 'yes'}})

    def test_arguments_validated_before_remote_execution(self):
        session = types.SimpleNamespace(call_tool=AsyncMock())
        for arguments in ({'id': 1}, {'id': 'A', 'extra': True}, None):
            with self.assertRaises(FileProblem) as caught:
                asyncio.run(invoke(session, TOOL, arguments))
            self.assertEqual(caught.exception.status, 400)
        session.call_tool.assert_not_called()

    def test_mcp_error_preserved_and_nontext_rejected(self):
        item = types.SimpleNamespace(model_dump=lambda **kwargs: {'type': 'text', 'text': 'permission denied'})
        session = types.SimpleNamespace(call_tool=AsyncMock(return_value=types.SimpleNamespace(content=[item], isError=True)))
        result = asyncio.run(invoke(session, TOOL, {'id': 'A'}))
        self.assertTrue(result['is_error'])
        item.model_dump = lambda **kwargs: {'type': 'image', 'data': 'x'}
        with self.assertRaises(FileProblem):
            asyncio.run(invoke(session, TOOL, {'id': 'A'}))
        self.assertEqual(session.call_tool.await_count, 2)

    def test_request_constraints(self):
        for changes in ({'stream': 'true'}, {'model': 'claude-sonnet-4-6'}, {'mcp_servers': [SERVER, SERVER]}):
            with self.subTest(changes=changes), self.assertRaises(FileProblem):
                prepare_request({**request(), **changes})

    def test_real_tool_loop_shapes_and_failed_calls_not_replayed(self):
        from mcp_connector import alias
        name = alias('deepwiki', 'read')
        def reply(identity):
            return {'id': 'msg', 'type': 'message', 'role': 'assistant', 'model': 'webcc-prompt-v1',
                    'stop_reason': 'tool_use', 'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 2},
                    'content': [{'type': 'tool_use', 'id': identity, 'name': name, 'input': {'id': 'A'}}]}
        final = {**reply('unused'), 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Remote read failed.'}]}
        handler = types.SimpleNamespace(manager=types.SimpleNamespace(request_seconds=150), caller_key='owner')
        remote = MagicMock(side_effect=[[TOOL], FileProblem(502, 'unknown')])
        with patch('mcp_messages.Connections.operation', remote), patch('mcp_messages.infer', side_effect=[reply('tool1'), reply('tool2'), final]):
            result = execute(handler, request(), lambda: None)
        self.assertEqual(remote.call_count, 2)  # one directory, one execution
        self.assertEqual(result['model'], MODEL)
        self.assertEqual([b['type'] for b in result['content']], ['mcp_tool_use', 'mcp_tool_result', 'mcp_tool_use', 'mcp_tool_result', 'text'])
        self.assertTrue(result['content'][1]['is_error'])


class RuntimeStreamFailureTests(unittest.TestCase):
    def test_disconnect_cancels_and_enqueues_without_second_http_response(self):
        from runtime_api import messages
        manager = types.SimpleNamespace(tasks=MagicMock())
        manager.tasks.get.return_value = {'id': 'run_webcc_fixture', 'state': 'processing', 'expires': time.time() + 300}
        manager.tasks.transaction.return_value.__enter__.return_value = (MagicMock(), {})
        handler = types.SimpleNamespace(manager=manager, caller_key=None, path='/v1/messages',
            headers={'X-WebCC-Runtime': 'e2b-v1'}, respond=MagicMock(), close_connection=False,
            body=lambda limit: json.dumps({'model': 'webcc-runtime-v1', 'stream': True,
                'container': 'run_webcc_fixture', 'messages': [{'role': 'user', 'content': 'continue'}]}).encode())
        stream = MagicMock(started=True)
        stream.error.side_effect = OSError('closed')
        with patch('message_stream.Stream', return_value=stream), patch('runtime_api.finish_message', side_effect=OSError('closed')), patch('runtime_api.enqueue') as queued:
            messages(handler)
        manager.tasks.cancel.assert_called_once_with('run_webcc_fixture', 'platform')
        queued.assert_called_once()
        handler.respond.assert_not_called()
        self.assertTrue(handler.close_connection)


class StreamTests(unittest.TestCase):
    def test_unicode_json_citations_and_results(self):
        citation = {'type': 'char_location', 'document_index': 0, 'document_title': 'A',
                    'cited_text': '中文', 'start_char_index': 0, 'end_char_index': 2}
        message = {'id': 'msg', 'type': 'message', 'role': 'assistant', 'model': MODEL, 'stop_reason': 'end_turn',
                   'usage': {'input_tokens': 3, 'output_tokens': 5}, 'content': [
                   {'type': 'mcp_tool_use', 'id': 'tool1', 'name': 'read', 'server_name': 'deepwiki', 'input': {'id': '中文' * 300}},
                   {'type': 'mcp_tool_result', 'tool_use_id': 'tool1', 'content': [{'type': 'text', 'text': 'actual'}], 'is_error': False},
                   {'type': 'text', 'text': '中文' * 300, 'citations': [citation]}]}
        original = copy.deepcopy(message)
        parsed = [json.loads(e.decode().split('data: ', 1)[1]) for e in events(message)]
        self.assertEqual(message, original)
        self.assertEqual(parsed[0]['message']['usage']['output_tokens'], 0)
        deltas = [e['delta'] for e in parsed if e['type'] == 'content_block_delta']
        self.assertEqual(json.loads(''.join(d['partial_json'] for d in deltas if d['type'] == 'input_json_delta')), message['content'][0]['input'])
        self.assertEqual(''.join(d['text'] for d in deltas if d['type'] == 'text_delta'), message['content'][2]['text'])
        self.assertIn({'type': 'citations_delta', 'citation': citation}, deltas)
        self.assertEqual(parsed[-2]['usage']['output_tokens'], 5)
        self.assertEqual(parsed[-1]['type'], 'message_stop')


if __name__ == '__main__':
    unittest.main()
