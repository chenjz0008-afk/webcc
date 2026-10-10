import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('WEBCC_CLUSTER_TEST_URL'), 'Isolated cluster database not configured')
class ClusterTests(unittest.TestCase):
    def setUp(self):
        from cluster_state import ClusterState
        from shared_limits import SharedLimits
        self.url = os.environ['WEBCC_CLUSTER_TEST_URL']
        self.redis_url = os.environ['WEBCC_CLUSTER_TEST_REDIS']
        self.a = ClusterState(self.url)
        with self.a.pool.connection() as db:
            db.execute('TRUNCATE webcc_entities,webcc_occupancy')
        self.a.baseline = self.a.load()
        self.a.save({'accounts': {'a': {'id': 'a', 'status': 'ready', 'name': 'A'}}, 'api_keys': {}, 'update': {}})
        self.b = ClusterState(self.url)
        self.limits = SharedLimits(self.redis_url)
        self.limits.client.flushdb()

    def tearDown(self):
        self.a.pool.close(); self.b.pool.close()

    def test_shared_fifo_is_bounded_and_expired_waiters_do_not_block(self):
        import time
        from shared_limits import SharedLimits
        other = SharedLimits(self.redis_url)
        self.assertTrue(self.limits.queue_operation('join', 'older', size=2, seconds=.05))
        self.assertTrue(other.queue_operation('join', 'newer', size=2))
        self.assertFalse(other.queue_operation('join', 'overflow', size=2))
        self.assertTrue(self.limits.queue_operation('head', 'older'))
        self.assertFalse(other.queue_operation('head', 'newer'))
        self.assertTrue(other.queue_operation('join', 'background', background=True, size=2))
        self.assertTrue(other.queue_operation('head', 'background', background=True))
        time.sleep(.07)
        self.assertTrue(other.queue_operation('head', 'newer'))
        other.queue_operation('leave', 'newer')
        other.queue_operation('leave', 'background', background=True)
        self.assertEqual(other.client.zcard('webcc:queue:{interactive}'), 0)

    def test_waiter_for_busy_account_does_not_block_other_accounts(self):
        import threading, time
        from manager import Manager, Problem
        state = self.a.load(); state['accounts']['b'] = {'id': 'b', 'name': 'B', 'status': 'ready'}; self.a.save(state)
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right, patch.dict(os.environ, {'MANAGER_DATABASE_URL': self.url, 'MANAGER_REDIS_URL': self.redis_url}):
            one = Manager(left, 'image', 'api', 'admin', queue_seconds=2)
            two = Manager(right, 'image', 'api', 'admin', queue_seconds=2)
            held = one.acquire(allowed={'a'}); waiting = threading.Event(); stop = threading.Event(); errors = []
            def check():
                waiting.set()
                if stop.is_set(): raise Problem(499, 'test cancellation')
            def acquire_busy():
                try: two.acquire(allowed={'a'}, on_wait=check)
                except Problem as error: errors.append(error.status)
            thread = threading.Thread(target=acquire_busy); thread.start(); self.assertTrue(waiting.wait(1)); time.sleep(.05)
            try:
                free = one.acquire(allowed={'b'}); self.assertEqual(free['id'], 'b'); one.release(free)
            finally:
                stop.set(); one.release(held); thread.join(3); one.cluster.pool.close(); two.cluster.pool.close()
            self.assertEqual(errors, [499])
            self.assertEqual(self.limits.client.zcard('webcc:queue:{interactive}'), 0)

    def test_independent_fields_merge_and_conflicts_reject(self):
        from cluster_state import StateConflict
        left, right = self.a.load(), self.b.load()
        left['accounts']['a']['status'] = 'paused'
        right['accounts']['a']['name'] = 'Renamed'
        self.a.save(left); self.b.save(right)
        actual = self.a.load()['accounts']['a']
        self.assertEqual((actual['status'], actual['name']), ('paused', 'Renamed'))
        stale = copy.deepcopy(right); stale['accounts']['a']['status'] = 'disabled'
        with self.assertRaises(StateConflict): self.b.save(stale)

    def test_deletion_cannot_be_resurrected_by_stale_writer(self):
        from cluster_state import StateConflict
        left, right = self.a.load(), self.b.load()
        del left['accounts']['a']; self.a.save(left)
        right['accounts']['a']['name'] = 'Changed'
        with self.assertRaises(StateConflict): self.b.save(right)

    def test_owner_only_release_and_restart_keep_busy(self):
        token = self.a.reserve('a', 'node-1', 4)
        self.assertTrue(token)
        self.assertFalse(self.b.release('a', 'wrong-token'))
        self.assertIsNone(self.b.reserve('a', 'node-2', 4))
        self.assertTrue(self.b.busy('a'))
        self.assertTrue(self.a.release('a', token))
        self.assertTrue(self.b.reserve('a', 'node-2', 4))

    def test_agent_can_claim_each_reservation_only_once(self):
        token = self.a.reserve('a', 'node-1', 4)
        self.assertFalse(self.b.claim('a', 'wrong-token'))
        self.assertTrue(self.a.claim('a', token))
        self.assertFalse(self.b.release_unclaimed('a', token))
        self.assertFalse(self.b.claim('a', token))
        self.assertTrue(self.a.release('a', token))
        self.assertFalse(self.b.claim('a', token))

    def test_rejected_unclaimed_task_can_be_released(self):
        token = self.a.reserve('a', 'node-1', 4)
        self.assertFalse(self.b.release_unclaimed('a', 'wrong-token'))
        self.assertTrue(self.b.release_unclaimed('a', token))
        self.assertFalse(self.a.busy('a'))
        self.assertFalse(self.a.release('a', token))
        self.assertFalse(self.b.claim('a', token))

    def test_two_processes_cannot_double_assign(self):
        code = "import os;from cluster_state import ClusterState;s=ClusterState(os.environ['WEBCC_CLUSTER_TEST_URL']);print(bool(s.reserve('a','child',4)));s.pool.close()"
        def child(_):
            return subprocess.check_output([sys.executable, '-c', code], text=True).strip()
        with ThreadPoolExecutor(4) as pool:
            values = list(pool.map(child, range(4)))
        self.assertEqual(values.count('True'), 1)

    def test_manager_selects_other_free_account_across_instances(self):
        from manager import Manager
        state = self.a.load()
        state['accounts']['b'] = {'id': 'b', 'status': 'ready', 'name': 'B'}
        self.a.save(state)
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right, patch.dict(os.environ, {'MANAGER_DATABASE_URL': self.url, 'MANAGER_REDIS_URL': self.redis_url}):
            one = Manager(left, 'image', 'api', 'admin', queue_seconds=.02)
            two = Manager(right, 'image', 'api', 'admin', queue_seconds=.02)
            first, second = one.acquire(), two.acquire()
            self.assertNotEqual(first['id'], second['id'])
            one.release(first); two.release(second)
            one.cluster.pool.close(); two.cluster.pool.close()

    def test_node_management_lock_and_capacity_configuration(self):
        from cluster_state import StateConflict
        with self.a.operation('node-1'):
            with self.assertRaises(StateConflict):
                with self.b.operation('node-1'): pass
            with self.b.operation('node-2'): pass
        token = self.a.reserve('a', 'node-1', 4)
        self.a.release('a', token)
        with self.assertRaises(ValueError): self.b.reserve('a', 'node-2', 5)

    def test_unconfigured_remote_node_skipped_and_release_idempotent(self):
        from manager import Manager
        state = self.a.load()
        state['accounts']['a']['node_id'] = 'unconfigured'
        state['accounts']['b'] = {'id': 'b', 'status': 'ready', 'node_id': 'node-1'}
        self.a.save(state)
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'MANAGER_DATABASE_URL': self.url,
                'MANAGER_REDIS_URL': self.redis_url, 'MANAGER_NODE_ID': 'node-1'}):
            manager = Manager(root, 'image', 'api', 'admin')
            account = manager.acquire()
            self.assertEqual(account['id'], 'b')
            manager.release(account); manager.release(account)
            self.assertEqual(manager.total, 0)
            self.assertFalse(manager.cluster.busy('b'))
            manager.cluster.pool.close()

    def test_update_probe_reserves_only_explicit_updating_state(self):
        state = self.a.load()
        state['accounts']['a']['status'] = 'updating'
        self.a.save(state)
        self.assertIsNone(self.a.reserve('a', 'node-1', 4))
        token = self.a.reserve('a', 'node-1', 4, ('updating',))
        self.assertTrue(token)
        self.a.release('a', token)
        with self.assertRaises(ValueError): self.a.reserve('a', 'node-1', 4, ('disabled',))

    def test_shared_global_capacity(self):
        state = self.a.load()
        state['accounts'] = {str(i): {'id': str(i), 'status': 'ready'} for i in range(8)}
        self.a.save(state)
        with ThreadPoolExecutor(8) as pool:
            values = list(pool.map(lambda i: self.a.reserve(str(i), 'node', 4), range(8)))
        self.assertEqual(sum(bool(v) for v in values), 4)

    def test_shared_rate_limit_and_expiry(self):
        from shared_limits import SharedLimits
        second = SharedLimits(self.redis_url)
        with ThreadPoolExecutor(8) as pool:
            values = list(pool.map(lambda i: (self.limits if i % 2 else second).admit('caller', 2), range(8)))
        self.assertEqual(values.count(0), 2)
        self.limits.client.delete('webcc:rpm:caller')
        self.assertEqual(second.admit('caller', 2), 0)
        with patch.object(self.limits, 'window', side_effect=ConnectionError):
            with self.assertRaises(ConnectionError): self.limits.admit('caller', 2)

    def test_manager_instances_share_revocation_rate_and_occupancy(self):
        from manager import Manager, Problem
        from api_keys import KeyProblem
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right, patch.dict(os.environ, {'MANAGER_DATABASE_URL': self.url, 'MANAGER_REDIS_URL': self.redis_url}):
            one = Manager(left, 'image', 'api', 'admin', queue_seconds=.02)
            two = Manager(right, 'image', 'api', 'admin', queue_seconds=.02)
            key = one.api_keys.create({'name': 'shared', 'accounts': ['a'], 'rpm': 2})
            with patch.object(one, 'refresh', side_effect=AssertionError('Authentication must not load all accounts')):
                one.api_keys.authenticate(key['key'], 'messages')
            two.api_keys.authenticate(key['key'], 'messages')
            with self.assertRaises(KeyProblem) as caught: two.api_keys.authenticate(key['key'], 'messages')
            self.assertEqual(caught.exception.status, 429)
            account = one.acquire()
            with self.assertRaises(Problem) as caught: two.acquire()
            self.assertEqual(caught.exception.status, 429)
            one.release(account)
            acquired = two.acquire(); two.release(acquired)
            one.api_keys.revoke(key['id'])
            with self.assertRaises(KeyProblem) as caught: two.api_keys.check_active(key['id'])
            self.assertEqual(caught.exception.status, 401)
            one.cluster.pool.close(); two.cluster.pool.close()

    def test_shared_files_bytes_owner_delete_and_quota(self):
        from postgres_files import PostgresFiles
        from web_files import FileProblem
        left, right = PostgresFiles(self.a), PostgresFiles(self.b)
        with self.a.pool.connection() as db: db.execute('TRUNCATE files')
        item = left.put('A', 'data.txt', 'text/plain', b'exact shared bytes')
        self.assertEqual(right.get('A', item['id'])[6], b'exact shared bytes')
        with self.assertRaises(FileProblem): right.get('B', item['id'])
        right.delete('A', item['id'])
        with self.assertRaises(FileProblem): left.get('A', item['id'])
        def put(i):
            try: (left if i % 2 else right).put('A', 'a', 'text/plain', b'1234'); return True
            except FileProblem: return False
        with patch('web_files.OWNER_QUOTA', 8), ThreadPoolExecutor(4) as pool:
            self.assertEqual(sum(pool.map(put, range(4))), 2)


if __name__ == '__main__': unittest.main()
