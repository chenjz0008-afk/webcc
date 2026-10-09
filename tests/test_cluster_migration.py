import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('WEBCC_CLUSTER_TEST_URL'), 'Isolated cluster database not configured')
class MigrationTests(unittest.TestCase):
    def test_atomic_import_bytes_and_non_destructive_rerun(self):
        from cluster_state import ClusterState
        from postgres_files import PostgresFiles
        from deploy.migrate_state import migrate
        from deploy.export_state import export
        from web_files import FileStore
        url = os.environ['WEBCC_CLUSTER_TEST_URL']
        cluster = ClusterState(url); PostgresFiles(cluster)
        with cluster.pool.connection() as db: db.execute('TRUNCATE webcc_entities,webcc_occupancy,files')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {'accounts': {'a': {'id': 'a', 'status': 'ready'}}, 'api_keys': {}, 'update': {}}
            (root / 'registry.json').write_text(json.dumps(state))
            store = FileStore(root)
            item = store.put('owner', 'exact.txt', 'text/plain', b'Exact migration bytes')
            result = migrate(root, url)
            self.assertEqual(result['files'], 1)
            self.assertEqual(PostgresFiles(cluster).get('owner', item['id'])[6], b'Exact migration bytes')
            self.assertEqual(migrate(root, url, verify=True), result)
            with self.assertRaises(ValueError): migrate(root, url)
            destination = root / 'rollback'
            exported = export(destination, url)
            self.assertEqual(exported['files'], 1)
            self.assertEqual(migrate(destination, url, verify=True), result)
            self.assertEqual(FileStore(destination).get('owner', item['id'])[6], b'Exact migration bytes')
            token = cluster.reserve('a', 'node-1', 4)
            with self.assertRaises(ValueError): export(root / 'blocked-export', url)
            self.assertFalse((root / 'blocked-export').exists())
            cluster.release('a', token)
            with sqlite3.connect(store.path) as db: db.execute('UPDATE files SET data=?', (b'changed source',))
            with self.assertRaises(ValueError): migrate(root, url, verify=True)
        cluster.pool.close()


if __name__ == '__main__': unittest.main()
