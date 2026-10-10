"""Private ASGI worker relay with bounded buffering and durable task ownership."""
import asyncio
from contextlib import asynccontextmanager
import hmac
import os
import secrets
import ssl

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from manager import Manager, GET_ROUTES, POST_ROUTES
from urllib.parse import urlsplit


class NodeAgent:
    def __init__(self, manager, token, client=None):
        if not manager.cluster or len(token) < 32:
            raise ValueError('Node agent requires shared state and a strong private token')
        self.manager, self.token = manager, token
        self.client = client or httpx.AsyncClient(trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(manager.worker_seconds, connect=5, write=30, pool=5),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))
        self.tasks = set()
        self.slots = asyncio.Semaphore(manager.max_inflight)

    def error(self, payload, status):
        return JSONResponse(payload, status, headers={'X-WebCC-Node-Error': '1'})

    def authorized(self, request):
        return hmac.compare_digest(request.headers.get('authorization', '').encode(), ('Bearer ' + self.token).encode())

    def owned(self, identity, token):
        self.manager.refresh()
        account = self.manager.state['accounts'].get(identity)
        if not account or account.get('node_id', 'node-1') != self.manager.node_id:
            return None
        with self.manager.cluster.pool.connection() as db:
            row = db.execute('SELECT 1 FROM webcc_occupancy WHERE account_id=%s AND token=%s', (identity, token)).fetchone()
        return dict(account) if row else None

    async def forward(self, request: Request):
        if not self.authorized(request):
            return self.error({'error': 'Unauthorized node request'}, 401)
        route = request.headers.get('x-webcc-route', '')
        allowed = GET_ROUTES if request.method == 'GET' else POST_ROUTES
        parsed = urlsplit(route)
        if len(route) > 4096 or parsed.scheme or parsed.netloc or parsed.fragment or parsed.path not in allowed:
            return self.error({'error': 'Unsupported worker route'}, 400)
        from node_transport import semantic_headers
        try:
            forwarded = semantic_headers(request.headers)
        except ValueError:
            return self.error({'error': 'Invalid protocol header'}, 400)
        identity, reservation = request.headers.get('x-webcc-account', ''), request.headers.get('x-webcc-reservation', '')
        try:
            account = await asyncio.to_thread(self.owned, identity, reservation)
        except Exception:
            return self.error({'error': 'Shared ownership state unavailable'}, 503)
        if account is None:
            return self.error({'error': 'Account ownership or reservation mismatch'}, 403)
        profile = request.headers.get('x-webcc-worker-profile')
        if profile not in (None, 'client-tools'):
            return self.error({'error': 'Unsupported worker execution profile'}, 400)
        if self.slots.locked():
            return self.error({'error': 'Node capacity exhausted'}, 429)
        await self.slots.acquire()
        try:
            claimed = await asyncio.to_thread(self.manager.cluster.claim, identity, reservation)
        except Exception:
            self.slots.release()
            return self.error({'error': 'Shared task claim unavailable'}, 503)
        if not claimed:
            self.slots.release()
            return self.error({'error': 'Task was already claimed'}, 409)
        response, dispatched = None, False
        try:
            raw = bytearray()
            async for part in request.stream():
                if len(raw) + len(part) > 32 * 1024 * 1024:
                    return self.error({'error': 'Worker request exceeds 32 MiB'}, 413)
                raw.extend(part)
            port = account['port']
            if profile:
                from worker_profiles import endpoint
                port = await asyncio.to_thread(endpoint, self.manager, account, profile)
            upstream = self.client.build_request(request.method,
                'http://127.0.0.1:' + str(port) + route,
                content=bytes(raw) if request.method == 'POST' else None,
                headers={'content-type': 'application/json', 'anthropic-version': '2023-06-01', **forwarded,
                         'Authorization': 'Bearer ' + account['key'], 'Accept-Encoding': 'identity'})
            dispatched = True
            response = await self.client.send(upstream, stream=True)
        except Exception:
            # No successful completion was observed. Persistent occupancy remains for reconciliation.
            return self.error({'error': 'Worker connection failed; task state requires reconciliation'}, 502)
        finally:
            if response is None:
                self.slots.release()
                if not dispatched:
                    await asyncio.to_thread(self.manager.cluster.release, identity, reservation)
        queue, disconnected = asyncio.Queue(maxsize=4), asyncio.Event()

        async def offer(item):
            if disconnected.is_set():
                return
            put, closed = asyncio.create_task(queue.put(item)), asyncio.create_task(disconnected.wait())
            try:
                await asyncio.wait((put, closed), return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (put, closed):
                    if not task.done(): task.cancel()
                await asyncio.gather(put, closed, return_exceptions=True)

        async def producer():
            complete = False
            try:
                async with asyncio.timeout(self.manager.request_seconds):
                    async for chunk in response.aiter_raw():
                        await offer(chunk)
                    complete = True
            except Exception:
                await offer(RuntimeError('Incomplete worker response'))
            finally:
                await response.aclose()
                if complete:
                    try:
                        await asyncio.to_thread(self.manager.cluster.release, identity, reservation)
                    except Exception:
                        print('node_task_release_failed', identity, flush=True)
                else:
                    print('node_task_incomplete', identity, flush=True)
                self.slots.release()
                await offer(None)

        task = asyncio.create_task(producer())
        self.tasks.add(task); task.add_done_callback(self.tasks.discard)

        async def content():
            try:
                while True:
                    item = await queue.get()
                    if item is None: break
                    if isinstance(item, Exception): raise item
                    yield item
            finally:
                # Continue draining in the producer; release only after upstream EOF.
                disconnected.set()

        headers = {'Content-Type': response.headers.get('content-type', 'application/json'),
                   'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'}
        if response.headers.get('retry-after'):
            headers['Retry-After'] = response.headers['retry-after']
        return StreamingResponse(content(), status_code=response.status_code, headers=headers)

    async def health(self, request):
        if not self.authorized(request): return self.error({'error': 'Unauthorized node request'}, 401)
        return JSONResponse({'ok': True, 'node_id': self.manager.node_id, 'active_tasks': len(self.tasks)})

    def app(self):
        @asynccontextmanager
        async def lifespan(app):
            yield
            if self.tasks:
                done, pending = await asyncio.wait(self.tasks, timeout=self.manager.request_seconds)
                for task in pending: task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            await self.client.aclose()
            self.manager.cluster.pool.close()
        return Starlette(routes=[Route('/internal/forward', self.forward, methods=['GET', 'POST']),
                                 Route('/internal/health', self.health)], lifespan=lifespan)


def main():
    import uvicorn
    manager = Manager(os.environ.get('MANAGER_DATA', '/var/lib/clewdr-manager'),
        os.environ.get('MANAGER_IMAGE', 'ghcr.io/xerxes-2/clewdr:v0.13.5'),
        secrets.token_urlsafe(36), secrets.token_urlsafe(36),
        int(os.environ.get('MANAGER_MAX_INFLIGHT', '4')))
    app = NodeAgent(manager, os.environ.get('MANAGER_AGENT_TOKEN', '')).app()
    bind = os.environ.get('MANAGER_AGENT_BIND', '127.0.0.1')
    cert, key, ca = (os.environ.get(name) for name in ('MANAGER_AGENT_TLS_CERT', 'MANAGER_AGENT_TLS_KEY', 'MANAGER_AGENT_TLS_CA'))
    if bind not in ('127.0.0.1', '::1') and not all((cert, key, ca)):
        raise ValueError('Non-loopback agent requires TLS with client certificate verification')
    uvicorn.run(app, host=bind, port=int(os.environ.get('MANAGER_AGENT_PORT', '9010')),
        limit_concurrency=32, access_log=False, ssl_certfile=cert, ssl_keyfile=key,
        ssl_ca_certs=ca, ssl_cert_reqs=ssl.CERT_REQUIRED if ca else ssl.CERT_NONE)


if __name__ == '__main__': main()
