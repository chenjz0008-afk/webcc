import json
import os
import subprocess
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from web_files import FileProblem


@unittest.skipUnless(os.environ.get('WEBCC_CLUSTER_TEST_URL'), 'Isolated PostgreSQL not configured')
class CheckpointTests(unittest.TestCase):
    def setUp(self):
        from cluster_state import ClusterState
        from task_store import TaskStore
        self.cluster = ClusterState(os.environ['WEBCC_CLUSTER_TEST_URL'])
        self.store = TaskStore(self.cluster)
        self.manager = types.SimpleNamespace(tasks=self.store, admin_key='test-checkpoint-key')
        with self.cluster.pool.connection() as db:
            db.execute('TRUNCATE webcc_tasks,procrastinate_jobs CASCADE')

    def tearDown(self):
        self.cluster.pool.close()

    def create(self):
        from mcp_tasks import cipher
        from runtime_queue import enqueue
        credentials = cipher(self.manager).encrypt(json.dumps({'owner': 'platform', 'servers': []}).encode()).decode()
        return self.store.create('mcp', 'platform', {'credentials': credentials, 'toolsets': [], 'params': {'tools': [], 'messages': []},
            'output': [], 'ledger': {}, 'failed_calls': [], 'phase': 'planning', 'calls': 0, 'turns': 0,
            'window_turn': 0, 'window_start': 0, 'round_budget': 1}, enqueue, 600)

    def reply(self, identity='call-1'):
        return {'content': [{'type': 'tool_use', 'id': identity, 'name': 'remote', 'input': {'id': 'A'}}],
                'stop_reason': 'tool_use', 'usage': {'input_tokens': 1, 'output_tokens': 1}}

    def run_with(self, item, remote, identity='call-1'):
        from mcp_tasks import process
        with patch('mcp_tasks.Connections'), patch('mcp_tasks.directory', return_value=({}, [])), \
             patch('mcp_tasks.describe_call', side_effect=lambda block, mapping: block), \
             patch('mcp_tasks.prepare'), patch('mcp_tasks.call_tool', remote):
            process(self.manager, item['id'], MagicMock(return_value=self.reply(identity)))

    def test_dispatch_interrupted_then_restart_does_not_replay(self):
        item = self.create()
        remote = MagicMock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.run_with(item, remote)
        self.assertEqual(self.store.get(item['id'])['phase'], 'dispatching')
        self.run_with(item, remote)
        actual = self.store.get(item['id'])
        self.assertEqual(actual['state'], 'unknown')
        self.assertNotIn('credentials', actual)
        self.assertEqual(remote.call_count, 1)

    def test_error_result_not_replayed_under_new_call_id(self):
        from mcp_tasks import resume
        item = self.create()
        remote = MagicMock(return_value=(self.reply()['content'][0], {'type': 'mcp_tool_result', 'tool_use_id': 'call-1', 'is_error': True, 'content': []},
                                        {'type': 'tool_result', 'tool_use_id': 'call-1', 'is_error': True, 'content': 'Denied'}))
        self.run_with(item, remote)
        self.assertEqual(self.store.get(item['id'])['state'], 'waiting')
        resume(self.manager, item['id'], 'platform')
        self.run_with(item, remote, 'call-2')
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(self.store.get(item['id'])['state'], 'failed')

    def test_completed_checkpoint_not_reexecuted_and_foreign_resume_denied(self):
        from mcp_tasks import resume
        item = self.create()
        with self.store.transaction(item['id']) as (_, current):
            current.update(state='processing', phase='calling', plan=self.reply(), ledger={'call-1': 'completed'}, internal_results=[])
        remote = MagicMock()
        self.run_with(item, remote)
        remote.assert_not_called()
        with self.assertRaises(FileProblem) as caught:
            resume(self.manager, item['id'], 'foreign')
        self.assertEqual(caught.exception.status, 404)

    def test_execution_lock_released_after_process_killed(self):
        item = self.create()
        code = ('import os,sys,time;from cluster_state import ClusterState;from task_store import TaskStore;'
                'c=ClusterState(os.environ["WEBCC_CLUSTER_TEST_URL"]);s=TaskStore(c);'
                'ctx=s.execution(sys.argv[1]);assert ctx.__enter__();print("locked",flush=True);time.sleep(60)')
        child = subprocess.Popen([sys.executable, '-c', code, item['id']], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'locked')
            with self.store.execution(item['id']) as duplicate:
                self.assertFalse(duplicate)
            child.kill(); child.wait(5)
            with self.store.execution(item['id']) as released:
                self.assertTrue(released)
        finally:
            if child.poll() is None:
                child.kill(); child.wait(5)
            child.stdout.close()


if __name__ == '__main__':
    unittest.main()
