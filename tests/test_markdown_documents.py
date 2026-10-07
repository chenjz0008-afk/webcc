import tempfile
import unittest
from pathlib import Path
from experiments.markdown_documents import MarkdownDocuments


class MarkdownDocumentTests(unittest.TestCase):
    def test_real_file_edit_preserves_other_content(self):
        with tempfile.TemporaryDirectory() as temp:
            store = MarkdownDocuments(Path(temp) / 'documents')
            store['B'] = {**store['B'], 'quantity': 10, 'revision': 3}
            self.assertEqual(store['B']['quantity'], 10)
            self.assertTrue(store.verify())

    def test_invalid_edit_cannot_change_file(self):
        with tempfile.TemporaryDirectory() as temp:
            store = MarkdownDocuments(Path(temp) / 'documents')
            for fields in [{'id': '../A'}, {'currency': 'USD'}, {'revision': 4}, {'quantity': 0}]:
                with self.assertRaises(ValueError):
                    store['B'] = {**store['B'], 'quantity': 10, 'revision': 3, **fields}
            self.assertEqual(store.path('B').read_text(), store.original['B'])
            with self.assertRaises(ValueError):
                store.path('../A')
