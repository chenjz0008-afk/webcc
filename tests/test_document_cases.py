import tempfile
from pathlib import Path
import unittest
from tests.support.document_cases import Documents


class DocumentCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Documents(Path(self.temp.name) / 'documents')

    def tearDown(self):
        self.temp.cleanup()

    def edit(self):
        for identity in ('A', 'B', 'MISSING', 'PRIVATE'):
            self.store.read(identity)
        return self.store.edit({'id': 'B', 'paragraph_id': 'B-P2', 'revision': 1, 'text': self.store.expected})

    def test_exact_target_and_unchanged_structure(self):
        self.edit()
        self.assertTrue(self.store.verify())
        with self.assertRaises(ValueError):self.edit()
        self.assertTrue(self.store.verify())

    def test_citation_must_match_actual_file(self):
        self.edit(); citations = []
        for identity in ('A', 'B'):
            value, _ = self.store.read(identity)
            paragraph = value['paragraphs'][1]
            citations.append({'id': identity, 'paragraph_id': paragraph['paragraph_id'], 'line': paragraph['line'], 'quote': paragraph['text']})
        fields = {'citations': citations, 'errors': ['not_found', 'permission_denied']}
        self.store.submit(fields)
        citations[1]['line'] -= 1
        with self.assertRaises(ValueError):self.store.submit(fields)

    def test_denied_and_missing_do_not_read_files(self):
        self.assertEqual(self.store.read('PRIVATE'), ({'error': 'permission_denied', 'id': 'PRIVATE'}, True))
        self.assertEqual(self.store.read('MISSING'), ({'error': 'not_found', 'id': 'MISSING'}, True))
        with self.assertRaises(ValueError):self.store.read('../A')
        self.assertEqual(self.store.path('A').read_text(), self.store.original['A'])

    def test_review_cannot_be_batched_before_readback_results(self):
        from tests.support.document_cases import run_document_case
        def call(name, fields, identity):
            return {'type': 'tool_use', 'id': identity, 'name': name, 'input': fields}
        replies = iter([
            [call('read_document', {'id': identity}, 'read-' + identity) for identity in ('A', 'B', 'MISSING', 'PRIVATE')],
            [call('edit_paragraph', {'id': 'B', 'paragraph_id': 'B-P2', 'revision': 1, 'text': self.store.expected}, 'edit')],
            [call('read_document', {'id': 'B'}, 'read-back'), call('submit_review', {'citations': [], 'errors': []}, 'premature-review')]
        ])
        result = run_document_case(lambda _: {'status': 200, 'message': {'content': next(replies)}}, Path(self.temp.name) / 'separate-review')
        self.assertFalse(result['pass'])
        self.assertIn('separate turn', result['failure'])
