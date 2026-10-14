import asyncio
import types
import unittest
from unittest.mock import patch

import network_mcp
from api_keys import KeyProblem


class MCPNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_owner_is_isolated_per_concurrent_request(self):
        seen = []
        async def app(scope, receive, send):
            first = network_mcp.OWNER.get()
            await asyncio.sleep(.01)
            seen.append((first, network_mcp.OWNER.get()))
        fake = types.SimpleNamespace(api_key='main', api_keys=types.SimpleNamespace(authenticate=lambda token, scope: (token, frozenset())))
        async def request(token):
            scope = {'type': 'http', 'headers': [(b'authorization', ('Bearer ' + token).encode())]}
            await network_mcp.Authentication(app)(scope, None, None)
        with patch.object(network_mcp, 'manager', fake):
            await asyncio.gather(request('A'), request('B'))
        self.assertEqual(set(seen), {('A', 'A'), ('B', 'B')})
        with self.assertRaises(LookupError): network_mcp.OWNER.get()

    async def test_missing_revoked_credentials_and_oversize_do_not_execute(self):
        executed, packets = [], []
        async def app(*args): executed.append(1)
        async def send(value): packets.append(value)
        def authenticate(*args): raise KeyProblem(401, 'revoked')
        fake = types.SimpleNamespace(api_key='main', api_keys=types.SimpleNamespace(authenticate=authenticate))
        with patch.object(network_mcp, 'manager', fake):
            for headers, code in [([], 401), ([(b'authorization', b'Bearer revoked')], 401),
                                  ([(b'content-length', b'65537')], 413)]:
                packets.clear()
                await network_mcp.Authentication(app)({'type': 'http', 'headers': headers}, None, send)
                self.assertEqual(packets[0]['status'], code)
        self.assertFalse(executed)
