"""PostgreSQL entity persistence and durable request ownership."""
import copy
from contextlib import contextmanager
import secrets
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


class StateConflict(RuntimeError):
    pass


class ClusterState:
    def __init__(self, url):
        self.pool = ConnectionPool(url, min_size=1, max_size=4, timeout=5, open=True,
                                   kwargs={'options': '-c statement_timeout=5000 -c lock_timeout=3000'})
        self.pool.wait(timeout=10)
        with self.pool.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS webcc_entities (kind text NOT NULL, id text NOT NULL, body jsonb NOT NULL, PRIMARY KEY(kind,id))')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS webcc_key_digest ON webcc_entities ((body->>'hash')) WHERE kind='api_keys'")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS webcc_session_identity ON webcc_entities ((body->>'session_hash')) WHERE kind='accounts'")
            db.execute('CREATE TABLE IF NOT EXISTS webcc_cluster_config (name text PRIMARY KEY, value integer NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS webcc_occupancy (account_id text PRIMARY KEY, token text NOT NULL UNIQUE, node_id text NOT NULL, started_at timestamptz NOT NULL DEFAULT now())')
            db.execute('ALTER TABLE webcc_occupancy ADD COLUMN IF NOT EXISTS agent_started boolean NOT NULL DEFAULT false')
        self.baseline = self.load()

    @staticmethod
    def entities(state):
        result = {(kind, identity): body for kind in ('accounts', 'api_keys')
                  for identity, body in state.get(kind, {}).items()}
        result[('settings', 'update')] = state.get('update', {})
        return result

    def load(self):
        result = {'accounts': {}, 'api_keys': {}, 'update': {}}
        with self.pool.connection() as db:
            for kind, identity, body in db.execute('SELECT kind,id,body FROM webcc_entities'):
                if kind in ('accounts', 'api_keys'):
                    result[kind][identity] = body
                elif kind == 'settings' and identity == 'update':
                    result['update'] = body
        return result

    def key(self, identity=None, digest=None):
        with self.pool.connection() as db:
            if identity is not None:
                row = db.execute("SELECT body FROM webcc_entities WHERE kind='api_keys' AND id=%s", (identity,)).fetchone()
            else:
                row = db.execute("SELECT body FROM webcc_entities WHERE kind='api_keys' AND body->>'hash'=%s", (digest,)).fetchone()
            return row[0] if row else None

    def save(self, state):
        missing = object()
        old, new = self.entities(self.baseline), self.entities(state)
        changed = sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
        with self.pool.connection() as db:
            for key in changed:
                db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('entity:' + ':'.join(key),))
                row = db.execute('SELECT body FROM webcc_entities WHERE kind=%s AND id=%s FOR UPDATE', key).fetchone()
                current = row[0] if row else None
                before, after = old.get(key), new.get(key)
                if before is None or after is None:
                    if current != before:
                        raise StateConflict('Entity changed concurrently; reload before retrying')
                    merged = after
                else:
                    merged = copy.deepcopy(current)
                    if merged is None:
                        raise StateConflict('Entity was deleted concurrently')
                    for field in before.keys() | after.keys():
                        if before.get(field, missing) != after.get(field, missing):
                            if current.get(field, missing) != before.get(field, missing):
                                raise StateConflict('Field changed concurrently; reload before retrying')
                            if field in after:
                                merged[field] = after[field]
                            else:
                                merged.pop(field, None)
                if merged is None:
                    db.execute('DELETE FROM webcc_entities WHERE kind=%s AND id=%s', key)
                else:
                    db.execute('INSERT INTO webcc_entities VALUES (%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body', (*key, Jsonb(merged)))
        self.baseline = copy.deepcopy(state)

    @contextmanager
    def operation(self, node_id):
        with self.pool.connection() as db:
            name = 'webcc:node-operation:' + node_id
            held = db.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))', (name,)).fetchone()[0]
            db.commit()
            if not held:
                raise StateConflict('Another node administration operation is in progress')
            try:
                yield
            finally:
                db.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (name,))
                db.commit()

    def reserve(self, account_id, node_id, capacity, statuses=('ready',)):
        if not statuses or set(statuses) - {'ready', 'updating'}:
            raise ValueError('Unsupported reservation state')
        token = secrets.token_hex(24)
        with self.pool.connection() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended('webcc:capacity',0))")
            db.execute("INSERT INTO webcc_cluster_config VALUES ('max_inflight',%s) ON CONFLICT(name) DO NOTHING", (capacity,))
            agreed = db.execute("SELECT value FROM webcc_cluster_config WHERE name='max_inflight'").fetchone()[0]
            if agreed != capacity:
                raise ValueError('Global concurrency differs from central configuration')
            row = db.execute("SELECT body FROM webcc_entities WHERE kind='accounts' AND id=%s FOR UPDATE", (account_id,)).fetchone()
            if not row or row[0].get('status') not in statuses:
                return None
            if db.execute('SELECT count(*) FROM webcc_occupancy').fetchone()[0] >= capacity:
                return None
            inserted = db.execute('INSERT INTO webcc_occupancy(account_id,token,node_id) VALUES (%s,%s,%s) ON CONFLICT(account_id) DO NOTHING RETURNING token', (account_id, token, node_id)).fetchone()
            return inserted[0] if inserted else None

    def claim(self, account_id, token):
        with self.pool.connection() as db:
            return db.execute('UPDATE webcc_occupancy SET agent_started=true WHERE account_id=%s AND token=%s AND NOT agent_started RETURNING account_id', (account_id, token)).fetchone() is not None

    def release(self, account_id, token):
        with self.pool.connection() as db:
            return db.execute('DELETE FROM webcc_occupancy WHERE account_id=%s AND token=%s', (account_id, token)).rowcount == 1

    def release_unclaimed(self, account_id, token):
        with self.pool.connection() as db:
            return db.execute('DELETE FROM webcc_occupancy WHERE account_id=%s AND token=%s AND NOT agent_started', (account_id, token)).rowcount == 1

    def busy(self, account_id):
        with self.pool.connection() as db:
            return db.execute('SELECT 1 FROM webcc_occupancy WHERE account_id=%s', (account_id,)).fetchone() is not None
