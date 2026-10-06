import copy
import json
import unittest
from experiments.web_tools import build_prompt, parse_response
from experiments.tool_history import MAX_BYTES
from jsonschema import ValidationError

TOOLS = [{'name': 'read', 'input_schema': {'type': 'object', 'properties': {
    'id': {'enum': ['A', 'B']}, 'options': {'type': 'object', 'properties': {
        'sections': {'type': 'array', 'items': {'enum': ['table', 'summary']}}},
        'required': ['sections'], 'additionalProperties': False}},
    'required': ['id', 'options'], 'additionalProperties': False}}]


def history():
    def call(identity, doc):
        return {'type': 'tool_use', 'id': identity, 'name': 'read',
                'input': {'id': doc, 'options': {'sections': ['table']}}}
    return [{'role': 'user', 'content': 'Compare A and B.'},
            {'role': 'assistant', 'content': [call('call_A', 'A'), call('call_B', 'B')]},
            {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'call_B', 'content': [{'type': 'text', 'text': 'B = 19'}]},
                {'type': 'tool_result', 'tool_use_id': 'call_A', 'content': 'A = 7'},
                {'type': 'text', 'text': 'Compare these results.'}]}]


class HistoryTests(unittest.TestCase):
    def reject(self, value):
        with self.assertRaises(ValueError):
            build_prompt(TOOLS, value)

    def test_reversed_results_keep_identity(self):
        value = history()
        prompt = build_prompt(TOOLS, value)
        self.assertEqual(json.loads(prompt.split('\n', 1)[1])['history'], value)

    def test_result_id_mismatch(self):
        value = history(); value[-1]['content'][0]['tool_use_id'] = 'foreign'
        self.reject(value)

    def test_missing_result(self):
        value = history(); value[-1]['content'].pop(0)
        self.reject(value)

    def test_duplicate_result(self):
        value = history(); value[-1]['content'].insert(0, copy.deepcopy(value[-1]['content'][0]))
        self.reject(value)

    def test_duplicate_call(self):
        value = history(); value[1]['content'][1]['id'] = 'call_A'
        self.reject(value)

    def test_result_before_call(self):
        self.reject([history()[-1]])

    def test_delayed_result(self):
        value = history(); value.insert(2, {'role': 'user', 'content': 'Later'})
        self.reject(value)

    def test_text_before_results(self):
        value = history(); value[-1]['content'].insert(0, {'type': 'text', 'text': 'Results'})
        self.reject(value)

    def test_unresolved_last_turn(self):
        self.reject(history()[:-1])

    def test_error_and_empty_result(self):
        value = history(); value[-1]['content'][0]['is_error'] = True
        del value[-1]['content'][1]['content']
        build_prompt(TOOLS, value)

    def test_error_flag_type(self):
        value = history(); value[-1]['content'][0]['is_error'] = 'false'
        self.reject(value)

    def test_unsupported_blocks(self):
        for kind in ['thinking', 'image', 'document', 'server_tool_use']:
            value = history(); value[0]['content'] = [{'type': kind}]
            self.reject(value)

    def test_tool_result_unsupported_content(self):
        value = history(); value[-1]['content'][0]['content'] = [{'type': 'image'}]
        self.reject(value)

    def test_input_schema_nested_enum(self):
        value = history(); value[1]['content'][0]['input']['options']['sections'] = ['unknown']
        with self.assertRaises(ValidationError):
            build_prompt(TOOLS, value)

    def test_reused_id_later(self):
        value = history()
        value.extend([copy.deepcopy(value[1]), copy.deepcopy(value[-1])])
        self.reject(value)

    def test_parallel_disabled(self):
        calls = [{'name': 'read', 'input': b['input']} for b in history()[1]['content']]
        with self.assertRaises(ValueError):
            parse_response(json.dumps({'calls': calls, 'text': ''}), TOOLS,
                           {'type': 'any', 'disable_parallel_tool_use': True})

    def test_choice_rejected_before_request(self):
        for choice in [{}, {'type': 'tool', 'name': 'missing'}, {'type': 'any', 'disable_parallel_tool_use': 1}]:
            with self.assertRaises(ValueError):
                build_prompt(TOOLS, history(), choice)

    def test_capacity_bytes_unicode(self):
        self.reject([{'role': 'user', 'content': '字' * (MAX_BYTES // 3)}])

    def test_capacity_turns(self):
        self.reject([{'role': 'user', 'content': 'x'}] * 129)

    def test_capacity_depth(self):
        nested = 'x'
        for _ in range(35):
            nested = [nested]
        value = history(); value[-1]['content'][0]['content'] = nested
        self.reject(value)

    def test_schema_id_cannot_change_reference_base(self):
        tools = copy.deepcopy(TOOLS)
        tools[0]['input_schema']['$id'] = 'https://example.com/schema'
        with self.assertRaises(ValueError):
            build_prompt(tools, history())

    def test_local_reference(self):
        tools = [{'name': 'read', 'input_schema': {'type': 'object',
            '$defs': {'id': {'enum': ['A', 'B']}},
            'properties': {'id': {'$ref': '#/$defs/id'}}, 'required': ['id']}}]
        parsed = parse_response(json.dumps({'calls': [{'name': 'read', 'input': {'id': 'A'}}], 'text': ''}), tools)
        self.assertEqual(parsed['content'][0]['input'], {'id': 'A'})
        with self.assertRaises(ValidationError):
            parse_response(json.dumps({'calls': [{'name': 'read', 'input': {'id': 'C'}}], 'text': ''}), tools)

    def test_no_mutation(self):
        value = history(); before = copy.deepcopy(value)
        build_prompt(TOOLS, value)
        self.assertEqual(value, before)


if __name__ == '__main__':
    unittest.main()
