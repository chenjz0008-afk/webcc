import asyncio
import json
import os
import socket
import threading
import time
import unittest
from unittest.mock import patch


class AuthenticatedTransportTests(unittest.TestCase):
    def test_real_sdk_transport_separates_bearers_and_initializes_once(self):
        try:
            import httpx
            import uvicorn
            from mcp.server.fastmcp import FastMCP, Context
            from mcp_connector import Connections
        except ImportError:
            self.skipTest('MCP test dependencies are not installed')
        server = FastMCP('controlled-auth', stateless_http=True, json_response=True)
        observed = []
        @server.tool()
        def whoami(ctx: Context) -> str:
            observed.append(dict(ctx.request_context.request.headers))
            return {'Bearer test-a': 'owner-A', 'Bearer test-b': 'owner-B'}[ctx.request_context.request.headers['authorization']]
        app = server.streamable_http_app()
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import Response
        class Auth(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                if request.headers.get('authorization') not in {'Bearer test-a', 'Bearer test-b'}:
                    return Response(status_code=401)
                return await call_next(request)
        app.add_middleware(Auth)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(16)
        port = listener.getsockname()[1]
        worker = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False))
        thread = threading.Thread(target=worker.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        for _ in range(100):
            if worker.started:
                break
            time.sleep(.02)
        original = httpx.AsyncHTTPTransport.handle_async_request
        requests = []
        async def local_transport(transport, request):
            requests.append(json.loads(request.content) if request.content else {})
            headers = dict(request.headers)
            headers['host'] = '127.0.0.1:' + str(port)
            local = httpx.Request(request.method, 'http://127.0.0.1:' + str(port) + request.url.raw_path.decode(), headers=headers, content=request.content)
            backend = httpx.AsyncHTTPTransport()
            response = await original(backend, local)
            raw = await response.aread()
            await backend.aclose()
            return httpx.Response(response.status_code, headers=response.headers, content=raw)
        environment = {'MANAGER_MCP_HOSTS': 'mcp.deepwiki.com', 'MANAGER_TOOLS_PROXY': 'socks5h://test:1080'}
        try:
            with patch.dict(os.environ, environment), patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', local_transport):
                with Connections() as client:
                    base = {'type': 'url', 'name': 'controlled', 'url': 'https://mcp.deepwiki.com/mcp'}
                    a, b = {**base, 'authorization_token': 'test-a'}, {**base, 'authorization_token': 'test-b'}
                    self.assertEqual(client.operation(a)[0]['name'], 'whoami')
                    result = client.operation(a, 'whoami', {})
                    self.assertIn('owner-A', result['content'][0]['text'])
                    result = client.operation(b, 'whoami', {})
                    self.assertIn('owner-B', result['content'][0]['text'])
                self.assertEqual(sum(r.get('method') == 'initialize' for r in requests), 2)
                self.assertEqual(sum(r.get('method') == 'tools/list' for r in requests), 2)
                self.assertEqual(len(observed), 2)
                self.assertEqual([r['authorization'] for r in observed], ['Bearer test-a', 'Bearer test-b'])
                self.assertFalse(any('x-forwarded-for' in r for r in observed))
                self.assertTrue(all(r['user-agent'] == 'WebCC-MCP/1' for r in observed))
        finally:
            worker.should_exit = True
            thread.join(5)
            listener.close()
