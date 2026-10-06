import json
import unittest
from jsonschema import ValidationError
from experiments.web_tools import build_prompt, parse_response

TOOLS = [{'name': 'read_document', 'input_schema': {'type': 'object',
          'properties': {'id': {'type': 'string'}}, 'required': ['id'],
          'additionalProperties': False}}]


class PromptToolsTests(unittest.TestCase):
    def parse(self, value, choice=None):
        return parse_response(json.dumps(value), TOOLS, choice)

    def test_call(self):
        result = self.parse({'calls': [{'name': 'read_document', 'input': {'id': 'a'}}], 'text': ''})
        self.assertEqual(result['stop_reason'], 'tool_use')
        self.assertTrue(result['content'][0]['id'].startswith('toolu_'))

    def test_final_with_quoted_call(self):
        text = 'Example: {"name":"read_document","input":{"id":"a"}}'
        result = self.parse({'calls': [], 'text': text})
        self.assertEqual(result['content'], [{'type': 'text', 'text': text}])

    def test_unknown_tool(self):
        with self.assertRaises(ValueError):
            self.parse({'calls': [{'name': 'delete_all', 'input': {}}], 'text': ''})

    def test_invalid_argument(self):
        for value in ({}, {'id': 1}, {'id': 'a', 'shell': 'rm'}):
            with self.assertRaises(ValidationError):
                self.parse({'calls': [{'name': 'read_document', 'input': value}], 'text': ''})

    def test_fenced_or_embedded_output(self):
        for text in ('```json\n{"calls":[],"text":"ok"}\n```', 'Example {"calls":[],"text":"ok"}', '{"calls":['):
            with self.assertRaises(ValueError):
                parse_response(text, TOOLS)

    def test_duplicate_key(self):
        with self.assertRaises(ValueError):
            parse_response('{"calls":[],"text":"a","text":"b"}', TOOLS)

    def test_external_schema_reference(self):
        with self.assertRaises(ValueError):
            build_prompt([{'name': 'unsafe', 'input_schema': {'type':'object', '$ref': 'https://example.com/schema'}}], [])

    def test_non_json_constant(self):
        with self.assertRaises(ValueError):
            parse_response('{"calls":[],"text":NaN}', TOOLS)

    def test_mixed_answer(self):
        with self.assertRaises(ValueError):
            self.parse({'calls': [{'name': 'read_document', 'input': {'id': 'a'}}], 'text': 'done'})

    def test_tool_choice(self):
        with self.assertRaises(ValueError):
            self.parse({'calls': [], 'text': 'ok'}, {'type': 'any'})
        with self.assertRaises(ValueError):
            self.parse({'calls': [{'name': 'read_document', 'input': {'id': 'a'}}], 'text': ''}, {'type': 'none'})

    def test_wrong_selected_tool(self):
        with self.assertRaises(ValueError):
            self.parse({'calls': [{'name': 'read_document', 'input': {'id': 'a'}}], 'text': ''}, {'type': 'tool', 'name': 'other'})

    def test_empty_final(self):
        with self.assertRaises(ValueError):
            self.parse({'calls': [], 'text': ''})

    def test_extra_envelope_field(self):
        with self.assertRaises(ValidationError):
            self.parse({'calls': [], 'text': 'ok', 'execute': True})

    def test_prompt_retains_tool_result_id(self):
        history = [{'role': 'user', 'content': 'Read a.'}, {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'toolu_123', 'name': 'read_document', 'input': {'id': 'a'}}]}, {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_123', 'is_error': True, 'content': 'not found'}]}]
        self.assertIn('toolu_123', build_prompt(TOOLS, history))
        self.assertIn('"is_error": true', build_prompt(TOOLS, history))


if __name__ == '__main__':
    unittest.main()
