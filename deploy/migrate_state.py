"""Offline, atomic import of a backed-up registry and SQLite file database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cluster_state import ClusterState
from postgres_files import PostgresFiles
from psycopg.types.json import Jsonb


def manifest(rows):
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        metadata = json.dumps(list(row[:6]), ensure_ascii=False, separators=(',', ':')).encode()
        digest.update(len(metadata).to_bytes(8, 'big'))
        digest.update(metadata)
        digest.update(hashlib.sha256(bytes(row[6])).digest())
        count += 1
    return count, digest.hexdigest()


def migrate(directory, url, verify=False):
    directory = Path(directory)
    state = json.loads((directory / 'registry.json').read_text())
    source = sqlite3.connect('file:' + str(directory / 'files.sqlite3') + '?mode=ro', uri=True) if (directory / 'files.sqlite3').exists() else None
    cluster = ClusterState(url)
    try:
        PostgresFiles(cluster)
        with cluster.pool.connection() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended('webcc:migration',0))")
            if not verify:
                if db.execute('SELECT count(*) FROM webcc_entities').fetchone()[0] or db.execute('SELECT count(*) FROM files').fetchone()[0]:
                    raise ValueError('Destination must be empty; refusing to overwrite live state')
                for (kind, identity), body in ClusterState.entities(state).items():
                    db.execute('INSERT INTO webcc_entities VALUES (%s,%s,%s)', (kind, identity, Jsonb(body)))
                if source:
                    for row in source.execute('SELECT * FROM files ORDER BY id'):
                        db.execute('INSERT INTO files VALUES (%s,%s,%s,%s,%s,%s,%s)', row)
            expected = manifest(source.execute('SELECT * FROM files ORDER BY id') if source else [])
            actual = manifest(db.execute('SELECT * FROM files ORDER BY id'))
            if expected != actual:
                raise ValueError('File metadata or byte digest does not match')
            target = {(kind, identity): body for kind, identity, body in db.execute('SELECT kind,id,body FROM webcc_entities')}
            if target != ClusterState.entities(state):
                raise ValueError('Registry entities do not match')
        return {'verified': True, 'accounts': len(state.get('accounts', {})), 'api_keys': len(state.get('api_keys', {})), 'files': actual[0], 'file_manifest_sha256': actual[1]}
    finally:
        if source: source.close()
        cluster.pool.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(migrate(args.directory, os.environ['MANAGER_DATABASE_URL'], args.verify)))
    except Exception as error:
        print(json.dumps({'verified': False, 'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
