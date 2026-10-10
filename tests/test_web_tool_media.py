import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from web_files import FileProblem, FileStore
from web_tools.api import prepare

TOOLS = [{'name': 'save', 'input_schema': {'type': 'object'}}]
FIXTURES = Path(__file__).parent / 'fixtures'


def image(raw=None):
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
            'data': base64.b64encode(raw or (FIXTURES / 'blocks.png').read_bytes()).decode()}}


def request(blocks):
    return {'model': 'webcc-prompt-v1', 'max_tokens': 1024, 'tools': TOOLS,
            'messages': [{'role': 'user', 'content': blocks}]}


class MediaTests(unittest.TestCase):
    def prepare(self, payload, **kwargs):
        data, body = prepare(json.dumps(payload), **kwargs)
        return data, json.loads(body)

    def test_large_attachment_not_prompt_json(self):
        payload=request([image(b'\x89PNG\r\n\x1a\n'+b'x'*150000), {'type':'text','text':'Save the image result'}])
        before=copy.deepcopy(payload); data, upstream=self.prepare(payload)
        self.assertEqual(payload,before)
        self.assertNotIn('source',data['messages'][0]['content'][0])
        self.assertEqual(upstream['messages'][0]['content'][-1],payload['messages'][0]['content'][0])
        self.assertIn('attachment_1',upstream['messages'][0]['content'][0]['text'])

    def test_pdf_metadata_and_bytes(self):
        pdf=(FIXTURES/'allocation.pdf').read_bytes()
        block={'type':'document','title':'North and South','source':{'type':'base64','media_type':'application/pdf','data':base64.b64encode(pdf).decode()}}
        data, upstream=self.prepare(request([block]))
        self.assertIn('North and South',data['messages'][0]['content'][0]['text'])
        self.assertEqual(base64.b64decode(upstream['messages'][0]['content'][-1]['source']['data']),pdf)

    def test_media_tool_result_retains_ids(self):
        payload=request([{'type':'text','text':'Read document'}]);payload['messages'] += [
            {'role':'assistant','content':[{'type':'tool_use','id':'toolu_real','name':'save','input':{}}]},
            {'role':'user','content':[{'type':'tool_result','tool_use_id':'toolu_real','content':[image(),{'type':'text','text':'actual image'}]}]}]
        data, upstream=self.prepare(payload)
        result=data['messages'][-1]['content'][0]
        self.assertEqual(result['tool_use_id'],'toolu_real');self.assertEqual(len(upstream['messages'][0]['content']),3)

    def test_owned_file_isolation_and_deleted_reference(self):
        with tempfile.TemporaryDirectory() as root:
            store=FileStore(Path(root));item=store.put('A','input.png','image/png',(FIXTURES/'blocks.png').read_bytes())
            payload=request([{'type':'image','source':{'type':'file','file_id':item['id']}}])
            self.prepare(payload,files=store,owner='A')
            with self.assertRaises(FileProblem) as caught:self.prepare(payload,files=store,owner='B')
            self.assertEqual(caught.exception.status,404)
            store.delete('A',item['id'])
            with self.assertRaises(FileProblem):self.prepare(payload,files=store,owner='A')

    def test_invalid_media_and_role(self):
        for block in [image(b'bad'),{'type':'image','source':{'type':'url','url':'http://127.0.0.1'}},
                      {'type':'image','source':{'type':'base64','media_type':'application/pdf','data':'JVBERi0='}},
                      {'type':'document','citations':{'enabled':True},'source':{'type':'text','data':'a'}}]:
            with self.assertRaises(ValueError):self.prepare(request([block]))
        payload=request([{'type':'text','text':'start'}]);payload['messages'].append({'role':'assistant','content':[image()]})
        with self.assertRaises(ValueError):self.prepare(payload)
        for messages in [None,[None],['invalid'],3]:
            with self.assertRaises(ValueError):self.prepare({**request([]),'messages':messages})

    def test_request_budget_and_attachment_count(self):
        with patch('web_tools.media.MAX_REQUEST',10):
            with self.assertRaises(FileProblem):self.prepare(request([image()]))
        with self.assertRaises(FileProblem):self.prepare(request([image() for _ in range(17)]))
        _,upstream=self.prepare(request([image(),image()]))
        self.assertIn('attachment_1',upstream['messages'][0]['content'][1]['text'])
        self.assertIn('attachment_2',upstream['messages'][0]['content'][3]['text'])
