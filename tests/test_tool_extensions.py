import copy
import json
import unittest
from web_tools import check_tools, build_prompt
from web_tools.api import prepare, complete, OutputProblem
from web_tools.discovery import search, visible_tools

TOOL = {'name': 'save', 'description': 'Save a document', 'input_schema': {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value'], 'additionalProperties': False}, 'input_examples': [{'value': 120}]}
SCHEMA = {'type': 'object', 'properties': {'total': {'const': 120}}, 'required': ['total'], 'additionalProperties': False}


def request(**kwargs):
    return {'model': 'webcc-prompt-v1', 'max_tokens': 512, 'messages': [{'role': 'user', 'content': 'Compute total'}], **kwargs}


def reply(text, reason='end_turn'):
    return json.dumps({'content': [{'type': 'text', 'text': json.dumps({'calls': [], 'text': text})}], 'stop_reason': reason, 'usage': {'input_tokens': 12, 'output_tokens': 8}}).encode()


class ExtensionTests(unittest.TestCase):
    def test_examples_validate_before_forward(self):
        check_tools([TOOL])
        invalid = copy.deepcopy(TOOL); invalid['input_examples'] = [{'value': '120'}]
        with self.assertRaises(Exception):
            check_tools([invalid])
        self.assertIn('input_examples', build_prompt([TOOL], request()['messages']))

    def test_structured_output_is_validated(self):
        payload, body = prepare(json.dumps(request(output_config={'format': {'type': 'json_schema', 'schema': SCHEMA}})))
        message = complete(reply('{"total":120}'), payload)
        self.assertEqual(json.loads(message['content'][0]['text']), {'total': 120})
        for invalid in ['{"total":119}', '{"total":120,"total":119}', '{"total":NaN}', 'not JSON']:
            with self.assertRaises(OutputProblem) as caught:
                complete(reply(invalid), payload)
            self.assertEqual(caught.exception.code, 'output_validation_failed')
        self.assertNotIn('output_config', json.loads(body))

    def test_truncation_and_refusal_are_not_retried(self):
        payload, _ = prepare(json.dumps(request(tools=[TOOL])))
        for reason, code in [('max_tokens', 'output_truncated'), ('refusal', 'output_refused')]:
            with self.assertRaises(OutputProblem) as caught:
                complete(reply('', reason), payload)
            self.assertEqual(caught.exception.code, code)
            self.assertFalse(caught.exception.retry)

    def test_discovery_does_not_expose_unloaded_tools(self):
        deferred = {**TOOL, 'defer_loading': True}
        loaded = {'name': 'find', 'input_schema': {'type': 'object'}, 'description': 'Find tools'}
        tools = [loaded, deferred]
        history = request()['messages']
        self.assertEqual([t['name'] for t in visible_tools(tools, history)], ['find'])
        found = search({'tools': tools, 'query': 'save document', 'limit': 1})
        self.assertEqual(found['tool_references'], [{'type': 'tool_reference', 'tool_name': 'save'}])
        history += [{'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'toolu_find', 'name': 'find', 'input': {}}]}, {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_find', 'content': found['tool_references']}]}]
        build_prompt(tools, history)
        self.assertEqual(len(visible_tools(tools, history)), 2)
        history[-1]['content'][0]['is_error'] = True
        self.assertEqual(len(visible_tools(tools, history)), 1)

    def test_external_schema_reference_is_rejected(self):
        tool = copy.deepcopy(TOOL)
        tool['input_schema']['properties']['value'] = {'$ref': 'https://example.com/schema'}
        with self.assertRaises(ValueError):
            check_tools([tool])


if __name__ == '__main__':
    unittest.main()
