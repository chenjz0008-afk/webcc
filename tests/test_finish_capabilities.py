import asyncio
import io
import json
import os
import unittest
from unittest.mock import patch
from document_citations import prepare, verify
from skill_api import upload
from upstream_message import read, UnexpectedTool
from web_files import FileProblem


class CitationTests(unittest.TestCase):
    def source(self, text):
        return [{'type': 'char_location', 'title': 'Example', 'pages': [text]}]

    def test_unicode_offset_computed_from_unique_quote(self):
        value = {'answer': '北区预算37', 'quotes': [{'document_index': 0, 'page': 1, 'start': 0, 'quote': '北区预算37'}]}
        result = verify(value, self.source('标题\n北区预算37。南区预算83。'))
        citation = result[0]['citations'][0]
        self.assertEqual(citation['start_char_index'], 3)
        self.assertEqual(citation['end_char_index'], 9)

    def test_unique_quote_needs_no_model_generated_character_count(self):
        value = {'answer': '北区预算37', 'quotes': [{'document_index': 0, 'page': 1, 'quote': '北区预算37'}]}
        result = verify(value, self.source('标题\n北区预算37。南区预算83。'))
        self.assertEqual(result[0]['citations'][0]['start_char_index'], 3)
        with self.assertRaises(FileProblem):
            verify(value, self.source('北区预算37。北区预算37。'))

    def test_ambiguous_quote_and_wrong_document_rejected(self):
        source = self.source('same quote / same quote')
        for quote in ({'document_index': 0, 'page': 1, 'start': 3, 'quote': 'same quote'},
                      {'document_index': 1, 'page': 1, 'start': 0, 'quote': 'same quote'},
                      {'document_index': 0, 'page': 2, 'start': 0, 'quote': 'same quote'},
                      {'document_index': 0, 'page': 1, 'start': 0, 'quote': 'not present'}):
            with self.assertRaises(FileProblem):
                verify({'answer': 'A', 'quotes': [quote]}, source)

    def test_pdf_page_exclusive_end(self):
        source = [{'type': 'page_location', 'title': 'PDF', 'pages': ['North 37', 'South 83']}]
        result = verify({'answer': '83', 'quotes': [{'document_index': 0, 'page': 2, 'start': 0, 'quote': 'South 83'}]}, source)
        self.assertEqual(result[0]['citations'][0]['end_page_number'], 3)

    def test_mixed_history_indices_and_context_excluded(self):
        fields = {'model': 'webcc-citations-v1', 'max_tokens': 512, 'messages': [{'role': 'user', 'content': [
            {'type': 'document', 'source': {'type': 'text', 'data': 'North 37'}, 'context': 'Unverified budget 999', 'citations': {'enabled': True}},
            {'type': 'text', 'text': 'What is north?'}]}]}
        request, sources = prepare(fields)
        self.assertEqual(sources[0]['pages'], ['North 37'])
        self.assertNotIn('999', json.dumps(request))
        self.assertEqual(request['model'], 'webcc-prompt-v1')


class SkillUploadTests(unittest.TestCase):
    def multipart(self, paths):
        body = b''
        for path, text in paths:
            body += ('--test\r\nContent-Disposition: form-data; name="files[]"; filename="' + path + '"\r\nContent-Type: text/plain\r\n\r\n').encode() + text.encode() + b'\r\n'
        return body + b'--test--\r\n'

    def test_sdk_upload_and_path_boundaries(self):
        text = '---\nname: totals\ndescription: Add rows\n---\nRun sum.py.'
        bundle = upload('multipart/form-data; boundary=test', self.multipart([('totals/SKILL.md', text), ('totals/sum.py', 'print(120)')]))
        self.assertEqual(set(bundle['files']), {'SKILL.md', 'sum.py'})
        for paths in ([('totals/SKILL.md', text), ('other/script.py', 'x')],
                      [('totals/SKILL.md', text), ('totals/../secret', 'x')],
                      [('totals/SKILL.md', text), ('totals/SKILL.md', text)]):
            with self.assertRaises(FileProblem):
                upload('multipart/form-data; boundary=test', self.multipart(paths))


class UpstreamStreamTests(unittest.TestCase):
    def fixture(self, reason='end_turn', block=None):
        items = [{'type': 'message_start', 'message': {'id': 'msg', 'usage': {'input_tokens': 1, 'output_tokens': 0}}},
                 {'type': 'content_block_start', 'index': 0, 'content_block': block or {'type': 'text', 'text': ''}},
                 {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'actual'}},
                 {'type': 'message_delta', 'delta': {'stop_reason': reason}, 'usage': {'output_tokens': 5}},
                 {'type': 'message_stop'}]
        response = io.BytesIO(b''.join(('data: ' + json.dumps(item) + '\n\n').encode() for item in items))
        response.getheader = lambda *args: 'text/event-stream'
        return response

    def test_real_stop_reason_preserved(self):
        for reason in ('end_turn', 'max_tokens', 'refusal'):
            result = json.loads(read(self.fixture(reason)))
            self.assertEqual(result['stop_reason'], reason)
            self.assertEqual(result['content'][0]['text'], 'actual')

    def test_unexpected_server_tool_not_replayed(self):
        with self.assertRaises(UnexpectedTool):
            read(self.fixture(block={'type': 'tool_use', 'name': 'write_file'}))

    def test_json_server_tool_uses_the_same_non_replay_boundary(self):
        response = io.BytesIO(json.dumps({'content': [{'type': 'tool_use', 'name': 'write_file'}]}).encode())
        response.length = None
        response.getheader = lambda *args: 'application/json'
        with self.assertRaises(UnexpectedTool):
            read(response, thinking=True)


if __name__ == '__main__':
    unittest.main()
