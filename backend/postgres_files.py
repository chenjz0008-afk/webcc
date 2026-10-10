"""Shared PostgreSQL file storage; retains resource IDs and exact file bytes."""
from contextlib import contextmanager
import threading
import time
from web_files import FileStore


class CursorAdapter:
    def __init__(self, database):
        self.database = database
        self.write_locked = False

    def execute(self, sql, args=()):
        if sql.startswith(('DELETE', 'INSERT', 'UPDATE')) and not self.write_locked:
            self.database.execute("SELECT pg_advisory_xact_lock(hashtextextended('webcc:files:quota',0))")
            self.write_locked = True
        return self.database.execute(sql.replace('?', '%s'), args)


class PostgresFiles(FileStore):
    def __init__(self, cluster):
        self.cluster = cluster
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(2)
        with self.cluster.pool.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS files (id text PRIMARY KEY, owner text NOT NULL, filename text, mime text, created double precision, expires double precision, data bytea NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS file_owner ON files(owner,created)')
            db.execute('CREATE INDEX IF NOT EXISTS file_expiry ON files(expires)')

    def prune(self):
        with self.cluster.pool.connection() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended('webcc:files:quota',0))")
            return db.execute('''DELETE FROM files WHERE id IN
                (SELECT id FROM files WHERE expires<=%s ORDER BY expires LIMIT 100 FOR UPDATE SKIP LOCKED)''',
                (time.time(),)).rowcount

    @contextmanager
    def connect(self):
        with self.cluster.pool.connection() as db:
            yield CursorAdapter(db)
