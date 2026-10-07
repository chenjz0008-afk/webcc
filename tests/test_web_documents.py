import base64
import copy
import json
import unittest
from web_documents import prepare, DocumentProblem
import test_manager as base

PDF = base64.b64encode(b'%PDF-1.4\nsynthetic fixture\n%%EOF').decode()


def payload(**fields):
    block = {'type': 'document', 'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': PDF}, **fields}
    return {'model': 'claude-sonnet-4-6', 'max_tokens': 1024,
            'messages': [{'role': 'user', 'content': [block, {'type': 'text', 'text': 'Read this PDF'}]}]}


class DocumentTests(unittest.TestCase):
    def test_pdf_mapping_preserves_data_and_metadata(self):
        value = payload(title='Report', context='Compare both pages', citations={'enabled': False})
        self.assertTrue(prepare(value))
        blocks = value['messages'][0]['content']
        self.assertEqual(blocks[0]['text'], 'Document title: Report\nDocument context: Compare both pages')
        self.assertEqual(blocks[1], {'type': 'image', 'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': PDF}})
        self.assertFalse(prepare(value))

    def test_unsupported_features_and_invalid_data_rejected(self):
        for fields in [{'citations': {'enabled': True}}, {'citations': {'enabled': 1}}, {'cache_control': {'type': 'ephemeral'}},
                       {'source': {'type': 'url', 'url': 'http://127.0.0.1/private'}}, {'source': {'type': 'file', 'file_id': 'other-user'}},
                       {'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': 'invalid=='}},
                       {'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': base64.b64encode(b'not pdf').decode()}},
                       {'context': 1}]:
            value = payload(**fields); original = copy.deepcopy(value)
            with self.assertRaises(DocumentProblem):prepare(value)
            self.assertEqual(value, original)

    def test_other_inputs_unchanged(self):
        for value in [None, {}, {'messages': [{'role': 'user', 'content': 'Hi'}]}, {'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': {}}]}]}]:
            original = copy.deepcopy(value);self.assertFalse(prepare(value));self.assertEqual(value, original)

    def test_invalid_later_document_does_not_partially_modify(self):
        value = payload(); value['messages'].append(payload(citations={'enabled': True})['messages'][0]);original = copy.deepcopy(value)
        with self.assertRaises(DocumentProblem):prepare(value)
        self.assertEqual(value, original)


class DocumentGatewayTests(unittest.TestCase):
    setUp = base.ManagerTests.setUp
    tearDown = base.ManagerTests.tearDown
    request = base.ManagerTests.request
    assert_idle = base.ManagerTests.assert_idle

    def test_invalid_document_rejected_before_account(self):
        for fields in [{'citations': {'enabled': True}}, {'source': {'type': 'url', 'url': 'http://127.0.0.1/'}}]:
            status, _, raw = self.request('POST', '/v1/messages', payload(**fields))
            self.assertEqual(status, 400);self.assertEqual(json.loads(raw)['error']['type'], 'invalid_request_error')
        self.assertEqual(base.Worker.seen, []);self.assertEqual(self.manager.dispatches, {})
        self.assertEqual(self.manager.get_account(self.identity)['status'], 'ready');self.assert_idle()

    def test_pdf_reaches_worker_with_same_bytes(self):
        account = self.manager.get_account(self.identity)
        base.Worker.replies['Bearer ' + account['key']] = (200, json.dumps({'content': [{'type': 'text', 'text': 'PDF result'}]}).encode())
        self.assertEqual(self.request('POST', '/v1/messages', payload())[0], 200)
        block = json.loads(base.Worker.seen[-1]['body'])['messages'][0]['content'][0]
        self.assertEqual(block['type'], 'image');self.assertEqual(block['source']['data'], PDF);self.assert_idle()
