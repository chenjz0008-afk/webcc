import json
import types
import unittest

from web_tools import parse_response
from web_tools.api import prepare
from web_tools.streaming import ToolStream
from web_tools.api import complete, OutputProblem


TOOL = {'name': 'save', 'input_schema': {'type': 'object', 'properties': {
    'path': {'type': 'string'}, 'content': {'type': 'string'}},
    'required': ['path', 'content'], 'additionalProperties': False}}


class ToolProgressTests(unittest.TestCase):
    def frames(self, output):
        return [json.loads(line[6:]) for line in b''.join(output).splitlines() if line.startswith(b'data: ')]

    def test_strict_progress_is_live_but_tools_wait_for_whole_response_validation(self):
        tool = {**TOOL, 'strict': True}
        request = {'tools': [tool], 'messages': [], '_standard_tools': True}
        for width in (1, 7, 4096):
            with self.subTest(width=width):
                output = []
                stream = ToolStream(types.SimpleNamespace(), 'owner', request, output.append, defer_tools=True)
                prefix = '{"text":"先读取文件。",'
                for offset in range(0, len(prefix), width):
                    stream.upstream({'type':'content_block_delta','delta':{'type':'text_delta','text':prefix[offset:offset+width]}})
                self.assertEqual(stream.text, '先读取文件。')
                suffix = '"calls":[{"name":"save","input":{"path":"报告/草稿.md","content":"a\\nb\\t\\\"c\\\""}}]}'
                for offset in range(0, len(suffix), width):
                    stream.upstream({'type':'content_block_delta','delta':{'type':'text_delta','text':suffix[offset:offset+width]}})
                self.assertFalse(any(f.get('content_block',{}).get('type')=='tool_use' for f in self.frames(output)))
                raw = json.dumps({'content':[{'type':'text','text':prefix+suffix}],'usage':{'input_tokens':1,'output_tokens':2}})
                message = complete(raw, request)
                stream.finish(message)
                frames = self.frames(output)
                calls = [f['content_block'] for f in frames if f['type']=='content_block_start' and f['content_block']['type']=='tool_use']
                self.assertEqual([b['id'] for b in calls], [b['id'] for b in message['content'] if b['type']=='tool_use'])
                arguments = ''.join(f.get('delta',{}).get('partial_json','') for f in frames)
                self.assertEqual(json.loads(arguments), {'path':'报告/草稿.md','content':'a\nb\t"c"'})
                self.assertEqual(frames[-1]['type'], 'message_stop')

    def test_strict_invalid_or_refused_reply_never_delivers_a_tool(self):
        request = {'tools':[{**TOOL,'strict':True}], 'messages':[], '_standard_tools':True}
        for reason, inputs in [('end_turn', {'path':'draft.md'}), ('refusal', {'path':'draft.md','content':'value'})]:
            output=[]
            stream=ToolStream(types.SimpleNamespace(), 'owner', request, output.append, defer_tools=True)
            text=json.dumps({'text':'Checking.','calls':[{'name':'save','input':inputs}]})
            stream.upstream({'type':'content_block_delta','delta':{'type':'text_delta','text':text}})
            raw=json.dumps({'content':[{'type':'text','text':text}], 'stop_reason':reason, 'usage':{'input_tokens':1,'output_tokens':2}})
            if reason=='refusal':
                message=complete(raw,request);stream.finish(message)
            else:
                with self.assertRaises(OutputProblem):complete(raw,request)
            self.assertFalse(any(f.get('content_block',{}).get('type')=='tool_use' for f in self.frames(output)))

    def test_strict_thinking_and_trailing_explanation_keep_order(self):
        from test_adapter_completion import manager
        for text_first in (False, True):
            m=manager();output=[]
            request={'tools':[{**TOOL,'strict':True}], 'messages':[], '_standard_tools':True}
            stream=ToolStream(m,'owner',request,output.append,defer_tools=True)
            stream.upstream({'type':'content_block_start','content_block':{'type':'thinking','thinking':''}})
            stream.upstream({'type':'content_block_delta','delta':{'type':'thinking_delta','thinking':'Upstream summary'}})
            stream.upstream({'type':'content_block_stop'})
            call={'name':'save','input':{'path':'draft.md','content':'Actual value'}}
            value=({'text':'Explanation','calls':[call]} if text_first else {'calls':[call],'text':'Explanation'})
            raw=json.dumps(value)
            for offset in range(0,len(raw),3):
                stream.upstream({'type':'content_block_delta','delta':{'type':'text_delta','text':raw[offset:offset+3]}})
            parsed=parse_response(raw,request['tools'])
            stream.finish({**parsed,'content':[{'type':'thinking','thinking':'Upstream summary','signature':'final'},*parsed['content']],
                           'usage':{'output_tokens':1}})
            starts=[f['content_block']['type'] for f in self.frames(output) if f['type']=='content_block_start']
            self.assertEqual(starts,['thinking',*(['text','tool_use'] if text_first else ['tool_use','text'])])

    def test_many_calls_and_definitions_have_shared_bounded_limits(self):
        from runtime_limits import TOOL_CALLS_MAX, TOOL_DEFINITIONS_MAX
        from web_tools import check_tools
        tools=[{**TOOL,'name':f'save_{i}'} for i in range(65)]
        check_tools(tools)
        call={'name':'save','input':{'path':'value','content':'text'}}
        value={'text':'','calls':[call]*12}
        expected=parse_response(json.dumps(value),[TOOL])
        output=[];stream=ToolStream(types.SimpleNamespace(),'owner',{'tools':[TOOL],'messages':[]},output.append)
        stream.upstream({'type':'content_block_delta','delta':{'type':'text_delta','text':json.dumps(value)}})
        stream.finish({**expected,'usage':{'output_tokens':1}})
        self.assertEqual(len(stream.calls),12)
        with self.assertRaises(Exception):parse_response(json.dumps({'text':'','calls':[call]*(TOOL_CALLS_MAX+1)}),[TOOL])
        with self.assertRaises(ValueError):check_tools([{**TOOL,'name':f't{i}'} for i in range(TOOL_DEFINITIONS_MAX+1)])

    def test_progress_precedes_tool_for_every_chunk_boundary(self):
        call = {'name': 'save', 'input': {'path': 'draft.md', 'content': '海外推广草稿'}}
        value = {'text': '先保存草稿，随后回读。', 'calls': [call]}
        raw = json.dumps(value, ensure_ascii=False)
        for strict in (False, True):
            tool = {**TOOL, 'strict': strict}
            expected = parse_response(raw, [tool])
            for width in (1, 7, len(raw)):
                with self.subTest(strict=strict, width=width):
                    output = []
                    stream = ToolStream(types.SimpleNamespace(), 'owner',
                                        {'tools': [tool], 'messages': []}, output.append)
                    for offset in range(0, len(raw), width):
                        stream.upstream({'type': 'content_block_delta',
                                         'delta': {'type': 'text_delta', 'text': raw[offset:offset + width]}})
                    stream.finish({**expected, 'usage': {'output_tokens': 1}})
                    frames = [json.loads(line[6:]) for line in b''.join(output).splitlines()
                              if line.startswith(b'data: ')]
                    starts = [f['content_block']['type'] for f in frames if f['type'] == 'content_block_start']
                    self.assertEqual(starts, ['text', 'tool_use'])
                    tool_start = next(i for i, f in enumerate(frames)
                                      if f['type'] == 'content_block_start' and f['content_block']['type'] == 'tool_use')
                    self.assertEqual(''.join(f.get('delta', {}).get('text', '') for f in frames[:tool_start]), value['text'])
                    self.assertEqual(frames[-2]['delta']['stop_reason'], 'tool_use')
                    self.assertEqual(frames[-1]['type'], 'message_stop')

    def test_analysis_and_code_examples_do_not_force_execution(self):
        for prompt in ('Only explain; do not modify files.', 'Now implement the approved change.'):
            fields = {'model': 'claude-sonnet-4-6', 'max_tokens': 1024, 'tools': [TOOL],
                      'messages': [{'role': 'user', 'content': prompt}]}
            request, raw = prepare(json.dumps(fields), standard=True)
            upstream = json.loads(raw)
            self.assertEqual(json.loads(upstream['messages'][0]['content'])['history'], fields['messages'])
            self.assertNotIn('tool_choice', upstream)
            self.assertNotIn('tool_choice', request)
        text = 'Example only:\n```python\ndef reserve():\n    pass\n```'
        parsed = parse_response(json.dumps({'calls': [], 'text': text}), [TOOL])
        self.assertEqual(parsed, {'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn'})
