"""Owned durable tasks and immutable skill versions, stored in PostgreSQL."""
import copy
import secrets
import time
from contextlib import contextmanager
from psycopg.types.json import Jsonb
from web_files import FileProblem, stamp


class TaskStore:
    def __init__(self, cluster):
        self.cluster = cluster
        with cluster.pool.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS webcc_tasks (
                id text PRIMARY KEY, owner text NOT NULL, kind text NOT NULL,
                state text NOT NULL, created double precision NOT NULL,
                expires double precision NOT NULL, body jsonb NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS webcc_tasks_owner ON webcc_tasks(owner,kind,created,id)')
            db.execute('''CREATE TABLE IF NOT EXISTS webcc_skills (
                id text NOT NULL, version text NOT NULL, owner text NOT NULL,
                created double precision NOT NULL, body jsonb NOT NULL,
                PRIMARY KEY(id,version))''')
            db.execute('CREATE INDEX IF NOT EXISTS webcc_skills_owner ON webcc_skills(owner,created,id)')

    @contextmanager
    def transaction(self, identity, owner=None):
        with self.cluster.pool.connection() as db:
            row = db.execute('SELECT owner,kind,state,created,expires,body FROM webcc_tasks WHERE id=%s FOR UPDATE', (identity,)).fetchone()
            if not row or owner is not None and owner != row[0]:
                raise FileProblem(404, 'Task not found')
            item = {'id': identity, 'owner': row[0], 'kind': row[1], 'state': row[2],
                    'created': row[3], 'expires': row[4], **row[5]}
            yield db, item
            body = {k: v for k, v in item.items() if k not in {'id', 'owner', 'kind', 'state', 'created', 'expires'}}
            db.execute('UPDATE webcc_tasks SET state=%s,expires=%s,body=%s WHERE id=%s', (item['state'], item['expires'], Jsonb(body), identity))

    @contextmanager
    def execution(self, identity):
        with self.cluster.pool.connection() as db:
            held = db.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))', ('runtime:' + identity,)).fetchone()[0]
            db.commit()
            try:
                yield held
            finally:
                if held:
                    db.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', ('runtime:' + identity,))
                    db.commit()

    def create(self, kind, owner, body, enqueue, ttl=86400):
        identity = ('msgbatch_' if kind == 'batch' else 'run_webcc_') + secrets.token_hex(16)
        now = time.time()
        with self.cluster.pool.connection() as db:
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('task-quota:' + owner,))
            active = db.execute("SELECT count(*) FROM webcc_tasks WHERE owner=%s AND state IN ('queued','processing','waiting','canceling')", (owner,)).fetchone()[0]
            total = db.execute('SELECT count(*) FROM webcc_tasks WHERE owner=%s', (owner,)).fetchone()[0]
            if active >= 16 or total >= 512:
                raise FileProblem(429, 'Task limit reached; remove finished tasks')
            db.execute('INSERT INTO webcc_tasks VALUES (%s,%s,%s,%s,%s,%s,%s)',
                       (identity, owner, kind, 'queued', now, now + ttl, Jsonb(body)))
            enqueue(db, identity)
        return self.get(identity, owner)

    def get(self, identity, owner=None):
        with self.cluster.pool.connection() as db:
            row = db.execute('SELECT owner,kind,state,created,expires,body FROM webcc_tasks WHERE id=%s', (identity,)).fetchone()
        if not row or owner is not None and row[0] != owner:
            raise FileProblem(404, 'Task not found')
        return {'id': identity, 'owner': row[0], 'kind': row[1], 'state': row[2],
                'created': row[3], 'expires': row[4], **row[5]}

    def list(self, owner, kind, query):
        from urllib.parse import parse_qs
        params = parse_qs(query, keep_blank_values=True)
        if set(params) - {'limit', 'after_id', 'before_id'} or any(len(v) != 1 for v in params.values()) or {'after_id', 'before_id'} <= params.keys():
            raise FileProblem(400, 'Invalid pagination')
        try:
            limit = int(params.get('limit', ['20'])[0])
        except ValueError:
            raise FileProblem(400, 'Invalid limit') from None
        if not 1 <= limit <= 1000:
            raise FileProblem(400, 'Limit must be 1 to 1000')
        before = 'before_id' in params
        field = 'before_id' if before else 'after_id'
        cursor = self.get(params[field][0], owner) if field in params else None
        if cursor and cursor['kind'] != kind:
            raise FileProblem(400, 'Invalid cursor kind')
        with self.cluster.pool.connection() as db:
            comparison, order = ('>', 'ASC') if before else ('<', 'DESC')
            rows = db.execute(f'''SELECT id FROM webcc_tasks WHERE owner=%s AND kind=%s
                AND (created,id){comparison}(%s,%s) ORDER BY created {order},id {order} LIMIT %s''',
                (owner, kind, cursor['created'] if cursor else float('inf'), cursor['id'] if cursor else '', limit + 1)).fetchall()
        chosen = rows[:limit]
        if before:
            chosen.reverse()
        return [self.get(r[0], owner) for r in chosen], len(rows) > limit

    def delete(self, identity, owner):
        with self.transaction(identity, owner) as (db, item):
            if item['state'] not in {'ended', 'failed', 'canceled', 'expired', 'unknown'} or item.get('cleanup_pending'):
                raise FileProblem(409, 'Cancel and wait for task cleanup before deleting')
            db.execute('DELETE FROM webcc_tasks WHERE id=%s', (identity,))
        return {'id': identity, 'type': 'task_deleted'}

    def cancel(self, identity, owner):
        with self.transaction(identity, owner) as (_, item):
            if item['state'] not in {'ended', 'failed', 'canceled', 'expired', 'unknown'}:
                item['cancel_requested'] = True
                item['state'] = 'canceling'
                item['cancel_initiated_at'] = time.time()
        return self.get(identity, owner)

    def skill_version(self, owner, identity, version):
        with self.cluster.pool.connection() as db:
            row = db.execute('SELECT body FROM webcc_skills WHERE owner=%s AND id=%s AND version=%s', (owner, identity, version)).fetchone()
        if not row:
            raise FileProblem(404, 'Skill version not found')
        return row[0]

    def put_skill(self, owner, bundle, identity=None):
        identity = identity or 'skill_webcc_' + secrets.token_hex(16)
        version = secrets.token_hex(12)
        with self.cluster.pool.connection() as db:
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('skill-quota:' + owner,))
            if identity and not identity.startswith('skill_webcc_'):
                raise FileProblem(400, 'Invalid skill ID')
            owners = db.execute('SELECT owner FROM webcc_skills WHERE id=%s LIMIT 1', (identity,)).fetchone()
            if owners and owners[0] != owner:
                raise FileProblem(404, 'Skill not found')
            existing = db.execute("SELECT body->>'name' FROM webcc_skills WHERE id=%s LIMIT 1", (identity,)).fetchone()
            if existing and existing[0] != bundle['name']:
                raise FileProblem(400, 'Skill name is immutable across versions')
            if db.execute('SELECT count(*) FROM webcc_skills WHERE owner=%s', (owner,)).fetchone()[0] >= 128:
                raise FileProblem(413, 'Skill version quota reached')
            db.execute('INSERT INTO webcc_skills VALUES (%s,%s,%s,%s,%s)', (identity, version, owner, time.time(), Jsonb(bundle)))
        return {'id': identity, 'type': 'skill', 'version': version, 'name': bundle['name'], 'description': bundle['description']}

    def skills(self, owner, identity=None):
        with self.cluster.pool.connection() as db:
            rows = db.execute('''SELECT id,version,created,body->>'name',body->>'description',body->>'display_name'
                FROM webcc_skills WHERE owner=%s AND (%s::text IS NULL OR id=%s)
                ORDER BY created DESC,id DESC LIMIT 128''', (owner, identity, identity)).fetchall()
        return [{'id': r[0], 'version': r[1], 'created_at': stamp(r[2]), 'name': r[3], 'description': r[4], 'display_name': r[5] or r[3], 'type': 'skill'} for r in rows]

    def delete_skill(self, owner, identity, version=None):
        with self.cluster.pool.connection() as db:
            # Submitted runs contain immutable snapshots and retain them until task deletion.
            count = db.execute('DELETE FROM webcc_skills WHERE owner=%s AND id=%s AND (%s::text IS NULL OR version=%s)', (owner, identity, version, version)).rowcount
        if not count:
            raise FileProblem(404, 'Skill not found')
        return {'id': identity, 'version': version, 'type': 'skill_deleted'}


def public_run(item):
    return {k: copy.deepcopy(item.get(k)) for k in ('id', 'state', 'result', 'error', 'pending_tools', 'cleanup_pending', 'progress')} | {
        'files': copy.deepcopy(item.get('output_files', [])),
        'type': 'run', 'created_at': stamp(item['created']), 'expires_at': stamp(item['expires'])}


def public_batch(item):
    counts = {k: 0 for k in ('processing', 'succeeded', 'errored', 'canceled', 'expired')}
    for request in item['requests']:
        counts[request.get('result', {}).get('type', 'processing')] += 1
    ended = item['state'] == 'ended'
    return {'id': item['id'], 'type': 'message_batch', 'processing_status': 'ended' if ended else 'canceling' if item.get('cancel_requested') else 'in_progress',
            'request_counts': counts, 'created_at': stamp(item['created']), 'expires_at': stamp(item['expires']),
            'ended_at': stamp(item['ended_at']) if ended else None,
            'cancel_initiated_at': stamp(item['cancel_initiated_at']) if item.get('cancel_initiated_at') else None,
            'results_url': '/v1/messages/batches/' + item['id'] + '/results' if ended else None}
