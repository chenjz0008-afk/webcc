import copy
import json
import socket
import threading
import time
import types
import unittest
import test_manager as base
from request_watch import watch


class BoundaryTests(unittest.TestCase):
    setUp = base.ManagerTests.setUp
    tearDown = base.ManagerTests.tearDown
    request = base.ManagerTests.request
    payload = base.ManagerTests.payload
    assert_idle = base.ManagerTests.assert_idle

    def test_native_extensions_and_beta_are_preserved(self):
        payload = {'model':'claude-sonnet-4-6','max_tokens':64,'messages':[{'role':'user','content':'Hi'}],
            'thinking':{'type':'adaptive'}, 'cache_control':{'type':'ephemeral'},
            'tools':[{'name':'web_search','type':'web_search_20250305'}]}
        account=self.manager.get_account(self.identity)
        output={'model':'actual-model','content':[{'type':'thinking','thinking':'A short summary','signature':'upstream-test-signature'},
                {'type':'text','text':'Hi'}],'stop_reason':'end_turn'}
        base.Worker.replies['Bearer '+account['key']] = (200,json.dumps(output).encode())
        status, _, raw = self.request('POST','/v1/messages?beta=true',payload,extra={'anthropic-beta':'test-extension'})
        self.assertEqual(status,200)
        self.assertEqual(json.loads(base.Worker.seen[-1]['body']),{**payload, 'stream': True})
        self.assertEqual(base.Worker.seen[-1]['headers']['anthropic-beta'],'test-extension')
        self.assertEqual(json.loads(raw),output)
        self.assert_idle()

    def test_capability_query_and_model_metadata(self):
        status, _, body = self.request('GET','/v1/capabilities')
        self.assertEqual(status,200)
        self.assertIn('native_thinking_signature',json.loads(body)['unverified'])
        account=self.manager.get_account(self.identity)
        base.Worker.replies['Bearer '+account['key']] = (200,b'{"model":"actual-model","content":[{"type":"text","text":"Hi"}],"stop_reason":"end_turn"}')
        status, headers, _ = self.request('POST','/v1/messages',{'model':'claude-sonnet-4-6','max_tokens':64,'messages':[{'role':'user','content':'Hi'}]})
        self.assertEqual(status,200)
        self.assertEqual(headers['X-WebCC-Requested-Model'],'claude-sonnet-4-6')
        self.assertEqual(headers['X-WebCC-Upstream-Model'],'actual-model')

    def test_unknown_model_metadata_is_not_invented(self):
        from manager import StreamState
        stream=StreamState();stream.feed(b'data: {"type":"message_start","message":{}}\n')
        self.assertEqual(stream.model,'unknown')
        stream.feed(b'data: {"type":"message_start","message":{"model":"actual-model"}}\n')
        self.assertEqual(stream.model,'actual-model')


class CancellationTests(unittest.TestCase):
    def test_disconnect_aborts_blocked_upstream_read(self):
        caller, peer = socket.socketpair();upstream, remote = socket.socketpair()
        handler=types.SimpleNamespace(connection=caller,check_caller=lambda:None)
        try:
            with watch(handler,types.SimpleNamespace(sock=upstream)) as (interrupted, reason):
                peer.close()
                self.assertTrue(interrupted.wait(2))
                self.assertEqual(upstream.recv(1),b'')
                self.assertEqual(type(reason[0]).__name__,'ClientGone')
        finally:
            caller.close();peer.close();upstream.close();remote.close()

    def test_revocation_interrupts_and_stops_monitor(self):
        from api_keys import KeyProblem
        caller,peer=socket.socketpair();upstream,remote=socket.socketpair()
        def revoked():raise KeyProblem(401,'revoked')
        try:
            with watch(types.SimpleNamespace(connection=caller,check_caller=revoked),types.SimpleNamespace(sock=upstream)) as (interrupted,reason):
                self.assertTrue(interrupted.wait(2))
                self.assertEqual(reason[0].status,401)
            self.assertEqual(upstream.recv(1),b'')
        finally:
            caller.close();peer.close();upstream.close();remote.close()
