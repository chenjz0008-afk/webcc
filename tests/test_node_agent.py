import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
import httpx
from node_agent import NodeAgent


class FakeDatabase:
    def execute(self, *args): return self
    def fetchone(self): return (1,)


class FakeCluster:
    def __init__(self): self.claimed = False; self.released = []
    @contextmanager
    def connection(self): yield FakeDatabase()
    @property
    def pool(self): return self
    def claim(self, identity, token):
        if self.claimed: return False
        self.claimed = True; return True
    def release(self, identity, token): self.released.append((identity, token)); return True


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'first'
        await asyncio.sleep(.02)
        yield b'last'


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cluster = FakeCluster(); self.seen = []
        self.manager = SimpleNamespace(cluster=self.cluster, refresh=lambda: None, node_id='node-1',
            state={'accounts': {'a': {'id': 'a', 'node_id': 'node-1', 'port': 12345, 'key': 'worker-secret'}}},
            max_inflight=4, worker_seconds=5, request_seconds=5)
        async def worker(request):
            self.seen.append(request)
            return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=SlowStream())
        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(worker))
        self.agent = NodeAgent(self.manager, 'x' * 40, self.upstream)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.agent.app()), base_url='http://node')
        self.headers = {'Authorization': 'Bearer ' + 'x' * 40, 'X-WebCC-Account': 'a',
            'X-WebCC-Reservation': 'reservation', 'X-WebCC-Route': '/v1/messages'}

    async def asyncTearDown(self):
        if self.agent.tasks: await asyncio.gather(*self.agent.tasks, return_exceptions=True)
        await self.client.aclose(); await self.upstream.aclose()

    async def test_auth_node_ownership_and_worker_headers(self):
        self.assertEqual((await self.client.post('/internal/forward')).status_code, 401)
        self.manager.state['accounts']['a']['node_id'] = 'node-2'
        rejected = await self.client.post('/internal/forward', headers=self.headers)
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.headers['x-webcc-node-error'], '1')
        self.assertFalse(self.seen)
        self.manager.state['accounts']['a']['node_id'] = 'node-1'
        response = await self.client.post('/internal/forward', headers={**self.headers, 'User-Agent': 'client-host-marker', 'X-Forwarded-For': 'client-address'}, content=b'{}')
        self.assertEqual(response.content, b'firstlast')
        self.assertNotIn('x-webcc-node-error', response.headers)
        request = self.seen[0]
        self.assertEqual(request.headers['authorization'], 'Bearer worker-secret')
        self.assertNotIn('client-host-marker', str(request.headers))
        self.assertNotIn('client-address', str(request.headers))
        self.assertEqual(self.cluster.released, [('a', 'reservation')])

    async def test_duplicate_task_cannot_dispatch(self):
        await self.client.post('/internal/forward', headers=self.headers, content=b'{}')
        response = await self.client.post('/internal/forward', headers=self.headers, content=b'{}')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.seen), 1)

    async def test_unknown_route_does_not_dispatch(self):
        response = await self.client.post('/internal/forward', headers={**self.headers, 'X-WebCC-Route': 'http://example.com'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.seen)

    async def test_disconnect_drains_before_release(self):
        from starlette.requests import Request
        headers = [(k.lower().encode(), v.encode()) for k, v in self.headers.items()]
        async def receive(): return {'type': 'http.request', 'body': b'{}', 'more_body': False}
        request = Request({'type': 'http', 'method': 'POST', 'path': '/internal/forward', 'headers': headers}, receive)
        response = await self.agent.forward(request)
        iterator = response.body_iterator
        self.assertEqual(await anext(iterator), b'first')
        self.assertFalse(self.cluster.released)
        await iterator.aclose()
        await asyncio.gather(*self.agent.tasks)
        self.assertEqual(self.cluster.released, [('a', 'reservation')])


if __name__ == '__main__': unittest.main()
