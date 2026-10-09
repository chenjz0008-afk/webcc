import copy
import json
import os
import tempfile
import types
import unittest
from unittest.mock import patch

from web_files import FileProblem


class SkillValidationTests(unittest.TestCase):
    def test_execution_caller_matches_allowed_version(self):
        from run_tasks import validate
        manager = types.SimpleNamespace(tasks=None)
        fields = {'code': 'print(120)', 'code_execution_type': 'code_execution_20260521',
                  'tools': [{'name': 'read', 'input_schema': {'type': 'object'},
                             'allowed_callers': ['code_execution_20260521']}]}
        self.assertEqual(validate(fields, manager, 'owner')['code_execution_type'], 'code_execution_20260521')
        fields['code_execution_type'] = 'code_execution_20260120'
        with self.assertRaises(FileProblem): validate(fields, manager, 'owner')

    def test_batch_expansion_is_bounded(self):
        from batch_tasks import validate_requests
        fields = {'requests': [{'custom_id': 'large', 'params': {
            'model': 'claude-sonnet-4-6', 'max_tokens': 64,
            'messages': [{'role': 'user', 'content': 'x'}]}}]}
        manager = types.SimpleNamespace(files=types.SimpleNamespace(
            resolve=lambda owner, params: ({**params, 'system': 'x' * 8388608}, None)))
        with self.assertRaises(FileProblem) as problem:
            validate_requests(fields, manager, 'owner', None)
        self.assertEqual(problem.exception.status, 413)

    def test_standard_bundle_and_unsafe_resources(self):
        from skill_bundles import validate_bundle
        source = {'files': {'SKILL.md': '---\nname: totals\ndescription: Sum rows\n---\nRead input and sum values.', 'scripts/total.py': 'print(120)'}}
        bundle = validate_bundle(source)
        self.assertEqual(bundle['name'], 'totals')
        for path in ('../secret', '/etc/passwd', 'scripts/../secret', 'a//b', 'a\\b'):
            with self.assertRaises(FileProblem):
                validate_bundle({'files': {**source['files'], path: 'x'}})
        for text in ('No metadata', '---\nname: invalid_name\ndescription: X\n---\nX', '---\nname: &n totals\ndescription: *n\n---\nX'):
            with self.assertRaises(FileProblem):
                validate_bundle({'files': {'SKILL.md': text}})

    def test_provider_refuses_direct_connections(self):
        from e2b_runtime import E2BRuntime
        for url in ('', 'http://localhost', 'https://proxy:443/?token=x'):
            with patch.dict(os.environ, {'E2B_API_KEY': 'test-key', 'MANAGER_E2B_PROXY': url}):
                with self.assertRaises(ValueError): E2BRuntime()


@unittest.skipUnless(os.environ.get('WEBCC_CLUSTER_TEST_URL'), 'Isolated PostgreSQL not configured')
class RuntimeDatabaseTests(unittest.TestCase):
    def setUp(self):
        from cluster_state import ClusterState
        from task_store import TaskStore
        from postgres_files import PostgresFiles
        self.cluster = ClusterState(os.environ['WEBCC_CLUSTER_TEST_URL'])
        self.store = TaskStore(self.cluster)
        self.files = PostgresFiles(self.cluster)
        with self.cluster.pool.connection() as db:
            db.execute('TRUNCATE webcc_tasks,webcc_skills,files')
        self.manager = types.SimpleNamespace(tasks=self.store, files=self.files)

    def tearDown(self):
        self.cluster.pool.close()

    def create(self, body=None, kind='run', owner='platform'):
        from runtime_queue import enqueue
        from run_tasks import validate
        body = body or validate({'code': 'print(120)'}, self.manager, owner)
        return self.store.create(kind, owner, body, enqueue)

    def test_enqueue_is_atomic_and_does_not_store_credentials(self):
        from runtime_queue import enqueue
        item = self.create()
        with self.cluster.pool.connection() as db:
            args = db.execute("SELECT args FROM procrastinate_jobs WHERE args->>'identity'=%s", (item['id'],)).fetchone()[0]
        self.assertEqual(args, {'identity': item['id']})
        def fail(db, identity):
            enqueue(db, identity)
            raise ValueError('rollback')
        with self.assertRaises(ValueError): self.store.create('run', 'B', {}, fail)
        self.assertEqual(self.store.list('B', 'run', '')[0], [])

    def test_expiry_job_does_not_block_resume_job(self):
        from runtime_queue import enqueue
        item = self.create()
        with self.cluster.pool.connection() as db:
            enqueue(db, item['id'], 300)
            enqueue(db, item['id'])
            rows = db.execute("SELECT lock FROM procrastinate_jobs WHERE args->>'identity'=%s", (item['id'],)).fetchall()
        self.assertEqual([r[0] for r in rows], [None, None, None])

    def test_expired_file_cleanup_preserves_active_resources(self):
        import time
        with self.cluster.pool.connection() as db:
            db.execute("INSERT INTO files VALUES ('expired-A','A','a.txt','text/plain',%s,%s,%s),('expired-B','B','b.txt','text/plain',%s,%s,%s),('active-A','A','live.txt','text/plain',%s,%s,%s)",
                       (time.time(),time.time()-1,b'a',time.time(),time.time()-1,b'b',time.time(),time.time()+100,b'live'))
        self.assertEqual(self.files.prune(),2)
        with self.cluster.pool.connection() as db:
            self.assertEqual(db.execute('SELECT id FROM files').fetchall(),[('active-A',)])

    def test_background_preserves_one_interactive_slot(self):
        from psycopg.types.json import Jsonb
        tokens = []
        try:
            with self.cluster.pool.connection() as db:
                for index in range(4):
                    identity = 'bg-' + str(index)
                    db.execute('INSERT INTO webcc_entities VALUES (%s,%s,%s)', ('accounts', identity, Jsonb({'id': identity, 'status': 'ready'})))
            for index in range(3):
                token = self.cluster.reserve('bg-' + str(index), 'node-1', 4, background=True)
                self.assertTrue(token); tokens.append(('bg-' + str(index), token))
            self.assertIsNone(self.cluster.reserve('bg-3', 'node-1', 4, background=True))
            token = self.cluster.reserve('bg-3', 'node-1', 4)
            self.assertTrue(token);tokens.append(('bg-3', token))
        finally:
            for identity, token in tokens:self.cluster.release(identity, token)
            with self.cluster.pool.connection() as db:db.execute("DELETE FROM webcc_entities WHERE kind='accounts' AND id LIKE 'bg-%%'")

    def test_canceling_sandbox_still_counts_toward_capacity(self):
        from run_tasks import process
        from unittest.mock import Mock
        for state in ('processing', 'canceling'):
            item = self.create()
            with self.store.transaction(item['id']) as (_, current):
                current.update(state=state, sandbox_slot=True)
        waiting = self.create()
        provider = types.SimpleNamespace(create=Mock())
        process(self.manager, waiting['id'], None, provider)
        provider.create.assert_not_called()
        self.assertEqual(self.store.get(waiting['id'])['state'], 'queued')

    def test_ownership_pagination_and_active_delete(self):
        a, b = self.create(), self.create()
        with self.assertRaises(FileProblem): self.store.get(a['id'], 'other')
        page, more = self.store.list('platform', 'run', 'limit=1')
        self.assertTrue(more)
        next_page, _ = self.store.list('platform', 'run', 'after_id=' + page[0]['id'])
        self.assertEqual(len(next_page), 1)
        with self.assertRaises(FileProblem): self.store.delete(a['id'], 'platform')
        with self.store.transaction(a['id']) as (_, item): item['state'] = 'ended'
        self.store.delete(a['id'], 'platform')
        with self.assertRaises(FileProblem): self.store.get(a['id'])

    def test_cross_instance_execution_lock_and_expiry(self):
        from task_store import TaskStore
        from cluster_state import ClusterState
        item = self.create()
        other = ClusterState(os.environ['WEBCC_CLUSTER_TEST_URL'])
        try:
            with self.store.execution(item['id']) as held:
                self.assertTrue(held)
                with TaskStore(other).execution(item['id']) as duplicate: self.assertFalse(duplicate)
            with self.store.transaction(item['id']) as (_, current): current['expires'] = 100
            self.assertEqual(self.store.get(item['id'])['expires'], 100)
        finally: other.pool.close()

    def test_skill_versions_snapshot_and_owner_isolation(self):
        from skill_bundles import validate_bundle, snapshot
        fields = {'files': {'SKILL.md': '---\nname: totals\ndescription: Sum rows\n---\nPrint 120'}}
        skill = self.store.put_skill('A', validate_bundle(fields))
        fields['files']['SKILL.md'] = fields['files']['SKILL.md'].replace('120', '200')
        new = self.store.put_skill('A', validate_bundle(fields), skill['id'])
        self.assertNotEqual(skill['version'], new['version'])
        saved = snapshot(self.store, 'A', [{'skill_id': skill['id'], 'version': skill['version']}])
        with self.assertRaises(FileProblem): snapshot(self.store, 'B', [{'skill_id': skill['id'], 'version': skill['version']}])
        self.store.delete_skill('A', skill['id'])
        self.assertIn('120', saved[0]['instructions'])

    def test_tool_results_atomic_duplicate_foreign_and_canceled(self):
        from run_tasks import resume
        item = self.create()
        call = {'id': 'toolu_' + 'a' * 32}
        with self.store.transaction(item['id']) as (_, current): current.update(state='waiting', pending_tools=[call])
        fields = {'tool_results': [{'tool_use_id': call['id'], 'content': '120'}]}
        with self.assertRaises(FileProblem): resume(self.store, item['id'], 'B', fields)
        with self.assertRaises(FileProblem): resume(self.store, item['id'], 'platform', {'tool_results': fields['tool_results'] * 2})
        self.assertEqual(self.store.get(item['id'])['delivered'], {})
        resume(self.store, item['id'], 'platform', fields)
        with self.assertRaises(FileProblem): resume(self.store, item['id'], 'platform', fields)
        self.store.cancel(item['id'], 'platform')
        with self.assertRaises(FileProblem): resume(self.store, item['id'], 'platform', fields)

    def test_batch_cancel_keeps_completed_and_never_replays_unknown(self):
        from batch_tasks import process
        requests = [{'custom_id': str(i), 'params': {'model': 'm'}, 'state': 'queued'} for i in range(3)]
        item = self.create({'requests': requests, 'mode': None}, 'batch')
        calls = []
        def infer(*args): calls.append(args); return {'content': [{'type': 'text', 'text': '120'}]}
        process(self.manager, item['id'], infer)
        self.store.cancel(item['id'], 'platform')
        process(self.manager, item['id'], infer); process(self.manager, item['id'], infer)
        result = self.store.get(item['id'])
        self.assertEqual(result['state'], 'ended')
        self.assertEqual([r['result']['type'] for r in result['requests']], ['succeeded', 'canceled', 'canceled'])
        self.assertEqual(len(calls), 1)
        item = self.create({'requests': [{'custom_id': 'unknown', 'state': 'processing'}]}, 'batch')
        process(self.manager, item['id'], infer)
        self.assertEqual(self.store.get(item['id'])['requests'][0]['result']['type'], 'errored')
        self.assertEqual(len(calls), 1)

    def test_runtime_pause_resume_file_and_cleanup(self):
        from run_tasks import process, resume, validate
        provider = FakeProvider()
        tool = {'name': 'fetch_total', 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}
        item = self.create(validate({'code': 'x=await fetch_total({});print(x)', 'tools': [tool], 'outputs': ['report.txt']}, self.manager, 'platform'))
        process(self.manager, item['id'], None, provider)
        self.assertEqual(self.store.get(item['id'])['state'], 'waiting')
        resume(self.store, item['id'], 'platform', {'tool_results': [{'tool_use_id': provider.call['id'], 'content': '120'}]})
        process(self.manager, item['id'], None, provider)
        completed = self.store.get(item['id'])
        self.assertEqual(completed['state'], 'ended')
        self.assertEqual(self.files.get('platform', completed['output_files'][0]['id'])[6], b'120')
        self.assertEqual((provider.starts, provider.kills, provider.pauses), (1, 1, 1))

    def test_invalid_tool_event_fails_before_client_and_cleans(self):
        from run_tasks import process
        provider = FakeProvider(); provider.call['name'] = 'undeclared'
        item = self.create()
        process(self.manager, item['id'], None, provider)
        result = self.store.get(item['id'])
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['pending_tools'], [])
        self.assertEqual(provider.kills, 1)

    def test_cancel_and_uncertain_start_do_not_rerun(self):
        from run_tasks import process
        item = self.create()
        provider = FakeProvider()
        self.store.cancel(item['id'], 'platform')
        process(self.manager, item['id'], None, provider)
        self.assertEqual(self.store.get(item['id'])['state'], 'canceled')
        self.assertEqual(provider.starts, 0)
        item = self.create()
        with self.store.transaction(item['id']) as (_, current): current.update(state='processing', sandbox_id='s', phase='starting')
        process(self.manager, item['id'], None, provider)
        self.assertEqual(self.store.get(item['id'])['state'], 'unknown')
        self.assertEqual(provider.starts, 0)


class FakeProvider:
    def __init__(self):
        self.starts = self.kills = self.pauses = 0
        self.delivered = False
        self.call = {'id': 'toolu_' + 'a' * 32, 'name': 'fetch_total', 'input': {}}
    def create(self, *args): return types.SimpleNamespace(sandbox_id='sandbox-test')
    def connect(self, *args): return types.SimpleNamespace(sandbox_id='sandbox-test')
    def start(self, *args): self.starts += 1; return 100
    def read_json(self, *args): return {'stdout': '120', 'stderr': '', 'error': None} if self.delivered else None
    def pending(self, *args): return [self.call]
    def deliver(self, *args): self.delivered = True
    def pause(self, *args): self.pauses += 1
    def kill(self, *args): self.kills += 1
    def output(self, *args): return b'120'
