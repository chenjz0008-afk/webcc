import json
import types
import unittest

from web_tools import parse_response
from web_tools.api import prepare
from web_tools.streaming import ToolStream


TOOL = {'name': 'save', 'input_schema': {'type': 'object', 'properties': {
    'path': {'type': 'string'}, 'content': {'type': 'string'}},
    'required': ['path', 'content'], 'additionalProperties': False}}


class ToolProgressTests(unittest.TestCase):
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
