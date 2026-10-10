import copy
import json
import types
import unittest
from unittest.mock import patch
from openai_tools import ChatResponse, convert
from web_tools import parse_response
from web_tools.streaming import ToolStream
from message_stream import events

TOOLS = [{'name': 'save', 'strict': True, 'input_schema': {'type': 'object', 'properties': {
    'id': {'type': 'string'}, 'version': {'type': 'integer'}}, 'required': ['id', 'version'], 'additionalProperties': False}}]


class EnvelopeTests(unittest.TestCase):
    def check_stream(self, value, width=1):
        request = {'tools': TOOLS, 'messages': [{'role': 'user', 'content': 'Save quote'}]}
        output = []
        stream = ToolStream(types.SimpleNamespace(), 'owner', request, output.append)
        raw = json.dumps(value, ensure_ascii=False)
        for offset in range(0, len(raw), width):
            stream.upstream({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': raw[offset:offset+width]}})
        message = {**parse_response(raw, TOOLS), 'usage': {'output_tokens': 10}}
        stream.finish(message)
        frames = [json.loads(line[6:]) for line in b''.join(output).splitlines() if line.startswith(b'data: ')]
        self.assertEqual(frames[-1]['type'], 'message_stop')
        arguments = ''.join(f['delta']['partial_json'] for f in frames if f.get('delta', {}).get('type') == 'input_json_delta')
        self.assertEqual(json.loads(arguments), {'id': '中文quote', 'version': 0})
        self.assertEqual(''.join(f['delta']['text'] for f in frames if f.get('delta', {}).get('type') == 'text_delta'), value.get('text', ''))
        return stream

    def test_real_failure_normalization_and_mixed_content_in_all_orders(self):
        call = {'name': 'save', 'input': {'id': '中文quote', 'version': 0}}
        for value in ({'calls': [{**call, 'text': ''}]}, {'calls': [call], 'text': '已准备草稿'},
                      {'text': '准备保存', 'calls': [call]}, {'calls': [{'input': call['input'], 'name': 'save'}], 'text': ''}, {'calls': [{**call, 'type': 'tool_use', 'id': 'toolu_model'}], 'text': ''}):
            original = copy.deepcopy(value)
            for width in (1, 7, 1024):
                self.check_stream(value, width)
            self.assertEqual(value, original)

    def test_invalid_arguments_and_unknown_data_are_not_repaired(self):
        for call in ({'name': 'save', 'input': {'id': 'A', 'version': '0'}},
                     {'name': 'save', 'input': {'id': 'A'}},
                     {'name': 'unknown', 'input': {}},
                     {'name': 'save', 'input': {'id': 'A', 'version': 0}, 'unexpected': ''},
                     {'name': 'save', 'input': {'id': 'A', 'version': 0}, 'type': 'execute'},
                     {'name': 'save', 'input': {'id': 'A', 'version': 0}, 'id': 'untyped'}):
            with self.subTest(call=call), self.assertRaises(Exception):
                parse_response(json.dumps({'calls': [call]}), TOOLS)

    def test_repeated_equal_call_name_is_normalized_once(self):
        raw = '{"calls":[{"name":"save","input":{"id":"中文quote","version":0},"name":"save"}],"text":""}'
        result = parse_response(raw, TOOLS)
        output = []; stream = ToolStream(types.SimpleNamespace(), 'owner', {'tools': TOOLS, 'messages': []}, output.append)
        for char in raw:
            stream.upstream({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': char}})
        stream.finish({**result, 'usage': {'output_tokens': 1}})
        self.assertEqual(b''.join(output).count(b'event: content_block_start'), 1)
        for bad in (raw.replace('"name":"save"}],', '"name":"other"}],'),
                    raw.replace('"version":0', '"version":0,"version":0')):
            with self.assertRaises(ValueError): parse_response(bad, TOOLS)

    def test_call_explanation_is_preserved_and_strict_arguments_wait_for_validation(self):
        request = {'tools': TOOLS, 'messages': []}; output = []
        stream = ToolStream(types.SimpleNamespace(), 'owner', request, output.append)
        first = '{"text":"准备保存","calls":[{"name":"save","input":{"id":"中文quote",'
        stream.upstream({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': first}})
        self.assertFalse(stream.emitted)
        last = '"version":0},"text":"保存草稿，不发送邮件"}]}'
        stream.upstream({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': last}})
        message = {**parse_response(first + last, TOOLS), 'usage': {'output_tokens': 1}}
        stream.finish(message)
        frames = [json.loads(l[6:]) for l in b''.join(output).splitlines() if l.startswith(b'data: ')]
        self.assertEqual(''.join(f.get('delta', {}).get('text', '') for f in frames), '准备保存保存草稿，不发送邮件')
        self.assertEqual(frames[-1]['type'], 'message_stop')

    def test_thinking_does_not_commit_an_invalid_tool_envelope(self):
        stream = ToolStream(types.SimpleNamespace(admin_key='fixture'), 'owner', {'tools': TOOLS, 'messages': []}, lambda data: self.fail('Invalid output committed'))
        stream.upstream({'type': 'content_block_start', 'content_block': {'type': 'thinking', 'thinking': 'Actual thought'}})
        stream.upstream({'type': 'content_block_stop'})
        stream.upstream({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'Not a JSON envelope'}})
        self.assertTrue(stream.failed)
        self.assertFalse(stream.emitted)

    def test_incremental_final_mismatch_cannot_complete(self):
        stream = self.check_stream({'calls': [{'name': 'save', 'input': {'id': '中文quote', 'version': 0}}], 'text': ''})
        with self.assertRaises(Exception):
            stream.finish({'content': [{'type': 'tool_use', 'name': 'save', 'input': {'id': 'other', 'version': 0}}], 'stop_reason': 'tool_use', 'usage': {'output_tokens': 1}})


class ChatTests(unittest.TestCase):
    def test_parallel_results_and_choice_round_trip(self):
        fields = {'model': 'actual-model', 'parallel_tool_calls': False, 'tool_choice': 'required',
                  'tools': [{'type': 'function', 'function': {'name': 'save', 'parameters': TOOLS[0]['input_schema'], 'strict': True}}],
                  'messages': [{'role': 'system', 'content': 'Only drafts'}, {'role': 'user', 'content': 'Save'},
                   {'role': 'assistant', 'content': 'Saving', 'tool_calls': [
                    {'id': 'call_a', 'function': {'name': 'save', 'arguments': '{"id":"A","version":0}'}},
                    {'id': 'call_b', 'function': {'name': 'save', 'arguments': '{"id":"B","version":0}'}}]},
                   {'role': 'tool', 'tool_call_id': 'call_b', 'content': 'B saved'},
                   {'role': 'tool', 'tool_call_id': 'call_a', 'content': 'A saved'}]}
        original = copy.deepcopy(fields)
        result = convert(fields)
        self.assertEqual(result['tool_choice'], {'type': 'any', 'disable_parallel_tool_use': True})
        self.assertEqual([b['tool_use_id'] for b in result['messages'][-1]['content']], ['call_b', 'call_a'])
        self.assertEqual(fields, original)

    def test_completed_results_can_be_sent_without_redeclaring_tools(self):
        from web_tools.api import prepare
        messages = [{'role': 'user', 'content': 'Read'}, {'role': 'assistant', 'content': None,
            'tool_calls': [{'id': 'call_done', 'function': {'name': 'read', 'arguments': '{"id":"A"}'}}]},
            {'role': 'tool', 'tool_call_id': 'call_done', 'content': 'Price 3.20'}]
        fields = convert({'model': 'model', 'messages': messages})
        request, _ = prepare(json.dumps(fields), standard=True)
        self.assertEqual(request['tools'], [])
        broken = copy.deepcopy(fields); broken['messages'][-1]['content'][0]['tool_use_id'] = 'other'
        with self.assertRaises(ValueError): prepare(json.dumps(broken), standard=True)

    def test_sdk_shape_fragmented_arguments_usage_and_error(self):
        message = {'id': 'msg', 'type': 'message', 'role': 'assistant', 'model': 'actual-model', 'stop_reason': 'tool_use',
                   'usage': {'input_tokens': 3, 'output_tokens': 5}, 'content': [
                    {'type': 'text', 'text': 'Saving quote'}, {'type': 'tool_use', 'id': 'tool_a', 'name': 'save', 'input': {'id': '中文quote', 'version': 0}}]}
        chat = ChatResponse(); chat.include_usage = True
        raw = b''.join(events(message)); output = b''.join(chat.feed(raw[i:i+3]) for i in range(0, len(raw), 3))
        packets = [json.loads(l[6:]) for l in output.splitlines() if l.startswith(b'data: {')]
        deltas = [p['choices'][0]['delta'] for p in packets if p['choices']]
        args = ''.join(d['tool_calls'][0]['function'].get('arguments', '') for d in deltas if 'tool_calls' in d)
        self.assertEqual(json.loads(args), message['content'][1]['input'])
        self.assertEqual(packets[-1]['usage']['total_tokens'], 8)
        self.assertIn(b'data: [DONE]', output)
        converted = chat.message(message)
        self.assertEqual(converted['choices'][0]['finish_reason'], 'tool_calls')
        self.assertEqual(converted['choices'][0]['message']['content'], 'Saving quote')
        self.assertEqual(chat.frame({'type': 'error', 'error': {'type': 'api_error', 'message': 'failed'}})[0]['error']['message'], 'failed')


class ChatGatewayTests(unittest.TestCase):
    from test_web_gateway import WebGatewayTests as Base
    setUp, tearDown, request, assert_idle = Base.setUp, Base.tearDown, Base.request, Base.assert_idle

    def test_default_openai_endpoint_uses_tools_and_results(self):
        from test_web_gateway import TOOLS as READ_TOOLS
        fields = {'model': 'claude-sonnet-4-6', 'max_tokens': 1024, 'tools': [{'type': 'function', 'function': {
            'name': 'read', 'parameters': READ_TOOLS[0]['input_schema']}}], 'messages': [{'role': 'user', 'content': 'Read A'}]}
        for streaming in (False, True):
            status, headers, data = self.request('POST', '/v1/chat/completions', {**fields, 'stream': streaming})
            self.assertEqual(status, 200)
            self.assertIn('standard-tools-v1', headers['X-WebCC-Adapter'])
            if streaming:
                self.assertIn(b'tool_calls', data); self.assertIn(b'[DONE]', data)
            else:
                value = json.loads(data)['choices'][0]['message']['tool_calls'][0]
                self.assertEqual(json.loads(value['function']['arguments']), {'id': 'A'})
            self.assert_idle()

    def test_completed_tool_then_late_stream_error_is_never_replayed(self):
        from test_web_gateway import Worker, TOOLS as READ_TOOLS
        from message_stream import event
        account = self.manager.get_account(self.identity)
        raw = json.dumps({'calls': [{'name': 'read', 'input': {'id': 'A'}}], 'text': '', 'unexpected': 'late-invalid-field'})
        sse = event('message_start', message={'id': 'msg', 'model': 'fixture', 'usage': {'input_tokens': 1}})
        sse += event('content_block_start', index=0, content_block={'type': 'text', 'text': ''})
        sse += event('content_block_delta', index=0, delta={'type': 'text_delta', 'text': raw})
        sse += event('message_delta', delta={'stop_reason': 'end_turn'}, usage={'output_tokens': 1})
        sse += event('message_stop')
        Worker.replies['Bearer ' + account['key']] = (200, sse, 'text/event-stream')
        status, _, data = self.request('POST', '/v1/messages', {'model': 'claude-sonnet-4-6', 'max_tokens': 1024,
            'stream': True, 'tools': READ_TOOLS, 'messages': [{'role': 'user', 'content': 'Read A'}]})
        self.assertEqual(status, 200)
        self.assertEqual(data.count(b'event: content_block_stop'), 1)
        self.assertIn(b'event: error', data)
        self.assertNotIn(b'event: message_stop', data)
        self.assertEqual(len(Worker.seen), 1)
        self.assertEqual(account.get('consecutive_failures', 0), 0)
        self.assert_idle()


class SourceAndOfficeTests(unittest.TestCase):
    def test_proxy_dns_is_public_and_address_is_pinned(self):
        from source_text import target
        for address in ('142.250.206.174', '127.0.0.1', '169.254.169.254'):
            response = types.SimpleNamespace(content=b'{}', raise_for_status=lambda: None,
                json=lambda: {'Status': 0, 'Answer': [{'type': 1, 'data': address}]})
            client = types.SimpleNamespace(get=lambda *a, **k: response)
            if address.startswith('142.'):
                pinned, host = target('https://support.google.com/help', client)
                self.assertEqual(pinned.host, address); self.assertEqual(host, 'support.google.com')
            else:
                with self.assertRaises(Exception): target('https://support.google.com/help', client)

    def test_csv_normalized_and_dependencies_checked_before_side_effects(self):
        from test_web_files import multipart
        from web_files import parse_upload, validate_content, FileProblem
        raw, content_type = multipart(mime='text/csv', filename='metrics.csv')
        self.assertEqual(parse_upload(content_type, raw)[1], 'text/plain')
        with self.assertRaises(FileProblem): validate_content('text/csv', b'\xff')
        from e2b_runtime import E2BRuntime
        commands = unittest.mock.MagicMock()
        commands.run.return_value = types.SimpleNamespace(exit_code=0, stdout='["pandas"]')
        sandbox = types.SimpleNamespace(commands=commands, files=unittest.mock.MagicMock())
        provider = object.__new__(E2BRuntime)
        with self.assertRaises(FileProblem) as caught:
            provider.start(sandbox, 'import pandas\nprint(1)', [], 30, [], [])
        self.assertEqual(caught.exception.status, 422)
        sandbox.files.write.assert_not_called()
        commands.run.assert_called_once()

    def test_large_valid_context_and_actionable_hard_boundary(self):
        from web_tools.history import bounded_json, MAX_BYTES
        self.assertGreater(len(bounded_json({'text': 'A' * 160000})), 160000)
        from web_files import FileProblem
        with self.assertRaises(FileProblem) as caught:
            bounded_json({'text': 'A' * MAX_BYTES})
        self.assertEqual(caught.exception.status, 413)
        self.assertIn('range reads', str(caught.exception))


class RuntimeUploadTests(unittest.TestCase):
    def test_container_upload_enters_sandbox_not_model_content(self):
        from runtime_api import messages
        from unittest.mock import MagicMock
        fields = {'model': 'claude-sonnet-4-6', 'max_tokens': 1024,
            'tools': [{'name': 'code_execution', 'type': 'code_execution_20260521'}],
            'messages': [{'role': 'user', 'content': [{'type': 'container_upload', 'file_id': 'file-A'},
                {'type': 'text', 'text': 'Analyze CSV'}]}]}
        original = copy.deepcopy(fields)
        manager = types.SimpleNamespace(tasks=MagicMock(), files=MagicMock())
        manager.files.get.return_value = ('file-A', 'platform', 'metrics.csv', 'text/plain', 0, None, b'private rows')
        manager.tasks.create.return_value = {'id': 'run', 'state': 'ended'}
        handler = types.SimpleNamespace(manager=manager, caller_key=None, path='/v1/messages', headers={})
        with patch('runtime_api.validate', side_effect=lambda request, *args: {**request, 'timeout_seconds': 300}) as validate, patch('runtime_api.finish_message'):
            messages(handler, fields, standard=True)
        request = validate.call_args.args[0]
        self.assertEqual(request['files'], [{'file_id': 'file-A', 'path': 'metrics.csv'}])
        self.assertEqual(request['messages'][0]['content'][0], {'type': 'text', 'text': 'Uploaded file is available at input/metrics.csv'})
        self.assertNotIn('private rows', json.dumps(request))
        self.assertEqual(fields, original)

    def test_html_extractor_accepts_large_bounded_page(self):
        import subprocess, sys
        from pathlib import Path
        article = '<html><body><article><h1>YouTube Shorts for businesses</h1>' + ('<p>Show actual products and direct viewers to wholesale inquiries. Keep claims grounded in your product catalog.</p>' * 20) + '</article><!--' + ('x' * 1100000) + '--></body></html>'
        result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve().parents[1] / 'html_text.py')], input=article.encode(), capture_output=True, timeout=6)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b'wholesale', result.stdout)
        rejected = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve().parents[1] / 'html_text.py')], input=b'x' * 2097153, capture_output=True, timeout=6)
        self.assertNotEqual(rejected.returncode, 0)


class CitationFailureTests(unittest.TestCase):
    def test_truncation_is_not_reported_as_bad_user_input(self):
        from document_citations import messages
        from web_files import FileProblem
        from unittest.mock import MagicMock
        fields = {'model': 'claude-sonnet-4-6', 'max_tokens': 64, 'messages': [{'role': 'user', 'content': [
            {'type': 'document', 'source': {'type': 'text', 'data': 'Source text'}, 'citations': {'enabled': True}}]}]}
        manager = types.SimpleNamespace(files=types.SimpleNamespace(resolve=lambda owner, f: (f, False)), processing_cache=None)
        handler = types.SimpleNamespace(manager=manager, caller_key=None, respond=MagicMock())
        truncated = {'content': [{'type': 'text', 'text': '{incomplete'}], 'stop_reason': 'max_tokens'}
        with patch('document_citations.infer', return_value=truncated), self.assertRaises(FileProblem) as caught:
            messages(handler, fields, standard=True)
        self.assertEqual(caught.exception.status, 502)
        self.assertIn('max_tokens', str(caught.exception))
        handler.respond.assert_not_called()
