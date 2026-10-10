"""Offline export for rollback to the previous single-node release."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from cluster_state import ClusterState


def export(directory, url):
    destination = Path(directory)
    if destination.exists():
        raise ValueError('Export destination must not exist')
    cluster = ClusterState(url)
    destination.mkdir(parents=True, mode=0o700)
    try:
        with cluster.pool.connection() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            if db.execute('SELECT count(*) FROM webcc_occupancy').fetchone()[0]:
                raise ValueError('Unresolved account occupancy prevents rollback export')
            state = {'accounts': {}, 'api_keys': {}, 'update': {}}
            for kind, identity, body in db.execute('SELECT kind,id,body FROM webcc_entities'):
                if kind in ('accounts', 'api_keys'): state[kind][identity] = body
                elif (kind, identity) == ('settings', 'update'): state['update'] = body
            path = destination / 'registry.json'
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2)); path.chmod(0o600)
            path = destination / 'files.sqlite3'
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600); os.close(fd)
            with sqlite3.connect(path) as sqlite:
                sqlite.execute('CREATE TABLE files (id TEXT PRIMARY KEY, owner TEXT NOT NULL, filename TEXT, mime TEXT, created REAL, expires REAL, data BLOB NOT NULL)')
                count = 0
                for row in db.execute('SELECT * FROM files ORDER BY id'):
                    sqlite.execute('INSERT INTO files VALUES (?,?,?,?,?,?,?)', row)
                    count += 1
        return {'exported': True, 'accounts': len(state['accounts']), 'api_keys': len(state['api_keys']), 'files': count}
    except Exception:
        import shutil
        shutil.rmtree(destination)
        raise
    finally:
        cluster.pool.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('directory'); args = parser.parse_args()
    try: print(json.dumps(export(args.directory, os.environ['MANAGER_DATABASE_URL'])))
    except Exception as error:
        print(json.dumps({'exported': False, 'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
