"""Remote MCP through the official SDK, isolated per request and explicit proxy."""
import asyncio
from contextlib import asynccontextmanager, ExitStack
from datetime import timedelta
import hashlib
import ipaddress
import json
import os
import re
import threading
from urllib.parse import urlsplit
from jsonschema import Draft202012Validator, ValidationError
from web_files import FileProblem
from web_tools import check_schema

SLOTS = threading.BoundedSemaphore(2)


def validate_server(server):
    if not isinstance(server, dict) or set(server) - {'type', 'name', 'url', 'authorization_token'}:
        raise FileProblem(400, 'Invalid MCP server definition')
    if server.get('type') != 'url' or not isinstance(server.get('name'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', server['name']):
        raise FileProblem(400, 'MCP requires type=url and a safe server name')
    url = server.get('url', '')
    if not isinstance(url, str) or len(url) > 2048:
        raise FileProblem(400, 'Invalid MCP URL')
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise FileProblem(400, 'Invalid MCP URL') from None
    hosts = {v.strip().lower() for v in os.environ.get('MANAGER_MCP_HOSTS', '').split(',') if v.strip()}
    if parsed.scheme != 'https' or not parsed.hostname or port not in (None, 443) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise FileProblem(400, 'MCP requires an HTTPS endpoint without credentials or query parameters')
    if parsed.hostname.lower() not in hosts:
        raise FileProblem(403, 'MCP host is not enabled by the administrator')
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise FileProblem(403, 'Private MCP addresses are not allowed')
    token = server.get('authorization_token')
    if token is not None and (not isinstance(token, str) or not token or len(token) > 4096 or any(c.isspace() for c in token)):
        raise FileProblem(400, 'Invalid MCP authorization token')
    proxy = os.environ.get('MANAGER_TOOLS_PROXY') or os.environ.get('MANAGER_E2B_PROXY', '')
    p = urlsplit(proxy)
    if p.scheme not in {'http', 'https', 'socks5', 'socks5h'} or not p.hostname or not p.port:
        raise FileProblem(503, 'MCP requires an explicit outbound proxy')
    return proxy


def configured_tools(tools, settings):
    if not isinstance(settings, dict) or set(settings) - {'type', 'mcp_server_name', 'default_config', 'configs'}:
        raise FileProblem(400, 'Unsupported MCP toolset')
    defaults, overrides = settings.get('default_config', {}), settings.get('configs', {})
    if not isinstance(defaults, dict) or not isinstance(overrides, dict):
        raise FileProblem(400, 'Invalid MCP tool configuration')
    for config in [defaults, *overrides.values()]:
        if not isinstance(config, dict) or set(config) - {'enabled', 'defer_loading'} or any(type(v) is not bool for v in config.values()):
            raise FileProblem(400, 'MCP configuration supports boolean enabled and defer_loading')
    result = []
    for tool in tools:
        config = {'enabled': True, 'defer_loading': False, **defaults, **overrides.get(tool['name'], {})}
        if config['enabled']:
            result.append({**tool, 'defer_loading': config['defer_loading']})
    return result


@asynccontextmanager
async def connect(server):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    proxy = validate_server(server)
    origin = urlsplit(server['url'])
    headers = {'User-Agent': 'WebCC-MCP/1', 'Accept-Encoding': 'identity'}
    if server.get('authorization_token'):
        headers['Authorization'] = 'Bearer ' + server['authorization_token']

    async def check_request(request):
        if request.url.scheme != 'https' or request.url.host != origin.hostname or request.url.port not in (None, 443):
            raise FileProblem(403, 'MCP redirect left its permitted origin')

    class LimitedStream(httpx.AsyncByteStream):
        def __init__(self, source):
            self.source = source

        async def __aiter__(self):
            size = 0
            async for chunk in self.source:
                size += len(chunk)
                if size > 1048576:
                    raise FileProblem(413, 'MCP response exceeds 1 MiB')
                yield chunk

        async def aclose(self):
            await self.source.aclose()

    class LimitedTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            response.stream = LimitedStream(response.stream)
            return response

    transport = LimitedTransport(proxy=proxy, retries=0)
    async with httpx.AsyncClient(transport=transport, headers=headers, trust_env=False, timeout=20,
                                 follow_redirects=False, event_hooks={'request': [check_request]}) as client:
        async with streamable_http_client(server['url'], http_client=client) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
                async with asyncio.timeout(20):
                    await session.initialize()
                yield session


async def catalog(session):
    result, cursors = [], set()
    cursor = None
    while True:
        page = await session.list_tools(cursor=cursor)
        for tool in page.tools:
            if len(result) >= 64 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', tool.name):
                raise FileProblem(413, 'MCP tool directory exceeds supported limits')
            check_schema(tool.inputSchema)
            result.append({'name': tool.name, 'description': (tool.description or '')[:4096], 'input_schema': tool.inputSchema})
        cursor = page.nextCursor
        if not cursor:
            break
        if cursor in cursors:
            raise FileProblem(502, 'MCP returned a repeated pagination cursor')
        cursors.add(cursor)
    if len({t['name'] for t in result}) != len(result) or len(json.dumps(result).encode()) > 131072:
        raise FileProblem(502, 'Invalid MCP tool directory')
    return result


async def invoke(session, tool, arguments):
    if not isinstance(arguments, dict):
        raise FileProblem(400, 'MCP arguments must be an object')
    try:
        Draft202012Validator(tool['input_schema']).validate(arguments)
    except ValidationError:
        raise FileProblem(400, 'MCP arguments failed schema validation') from None
    response = await session.call_tool(tool['name'], arguments=arguments)
    content = [item.model_dump(by_alias=True, exclude_none=True) for item in response.content]
    if any(b.get('type') != 'text' or not isinstance(b.get('text'), str) for b in content):
        raise FileProblem(502, 'MCP result type is not supported by this adapter')
    if len(json.dumps(content).encode()) > 65536:
        raise FileProblem(413, 'MCP tool output exceeds 64 KiB')
    return {'content': content, 'is_error': bool(response.isError)}


class Connections:
    def __enter__(self):
        from anyio.from_thread import start_blocking_portal
        if not SLOTS.acquire(blocking=False):
            raise FileProblem(429, 'MCP connections are busy')
        self.stack, self.sessions, self.directories, self.failed = ExitStack(), {}, {}, set()
        try:
            self.portal = self.stack.enter_context(start_blocking_portal())
        except BaseException:
            SLOTS.release()
            raise
        return self

    def __exit__(self, *error):
        try:
            return self.stack.__exit__(*error)
        except Exception as failure:
            print('mcp_close_failed', type(failure).__name__, flush=True)
            return False
        finally:
            SLOTS.release()

    def operation(self, server, name=None, arguments=None, timeout=30):
        validate_server(server)
        if name is not None and (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) or not isinstance(arguments, dict)):
            raise FileProblem(400, 'Invalid MCP tool call')
        identity = (server['name'], server['url'], server.get('authorization_token'))
        if identity in self.failed:
            raise FileProblem(502, 'MCP session failed; remote operations were not replayed')
        async def run():
            async with asyncio.timeout(max(.1, min(30, timeout))):
                session = self.sessions[identity]
                if identity not in self.directories:
                    self.directories[identity] = await catalog(session)
                tools = self.directories[identity]
                if name is None:
                    return tools
                tool = next((t for t in tools if t['name'] == name), None)
                if not tool:
                    raise FileProblem(400, 'MCP tool was not declared by this server')
                return await invoke(session, tool, arguments)
        try:
            if identity not in self.sessions:
                self.sessions[identity] = self.stack.enter_context(self.portal.wrap_async_context_manager(connect(server)))
            return self.portal.call(run)
        except FileProblem:
            raise
        except Exception:
            self.failed.add(identity)
            raise FileProblem(502, 'MCP connection or execution failed; operation was not retried') from None


def operation(server, name=None, arguments=None, timeout=30):
    with Connections() as connections:
        return connections.operation(server, name, arguments, timeout)


def route(handler, target):
    if target.path.startswith('/v1/mcp/sessions/'):
        from mcp_tasks import route as session_route
        session_route(handler, target)
        return
    handler.authenticate(scope='mcp')
    handler.adapter = 'mcp-connector-v1'
    if handler.command != 'POST' or target.query or target.path not in {'/v1/mcp/tools', '/v1/mcp/call'}:
        raise FileProblem(405, 'Unsupported MCP operation')
    fields = json.loads(handler.body(131072))
    allowed = {'server'} if target.path.endswith('/tools') else {'server', 'name', 'arguments'}
    if not isinstance(fields, dict) or set(fields) != allowed:
        raise FileProblem(400, 'Invalid MCP operation parameters')
    result = operation(fields['server'], fields.get('name'), fields.get('arguments'))
    handler.respond(200, {'data': result} if isinstance(result, list) else result)


def alias(server_name, tool_name):
    return 'mcp_' + hashlib.sha256((server_name + ':' + tool_name).encode()).hexdigest()[:24]
