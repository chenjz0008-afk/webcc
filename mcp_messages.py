"""Bounded MCP tool loop using the existing validated web-account adapter."""
import copy
import json
import threading
import time
from uuid import uuid4
from mcp_connector import alias, configured_tools, Connections, validate_server
from message_stream import Stream
from runtime_forward import infer
from web_files import FileProblem
from web_tools.api import prepare
from web_tools.history import bounded_json, load_json

SLOTS = threading.BoundedSemaphore(2)
MODEL = 'webcc-mcp-v1'


def prepare_request(fields):
    if not isinstance(fields, dict) or set(fields) - {'model', 'max_tokens', 'stream', 'messages', 'system', 'mcp_servers', 'tools'}:
        raise FileProblem(400, 'Unsupported MCP Messages fields')
    if fields.get('model') != MODEL or type(fields.get('stream', False)) is not bool:
        raise FileProblem(400, 'Use webcc-mcp-v1 and a boolean stream field')
    servers, sets = fields.get('mcp_servers'), fields.get('tools')
    if not isinstance(servers, list) or not 1 <= len(servers) <= 2 or not isinstance(sets, list) or len(sets) != len(servers):
        raise FileProblem(400, 'Declare one toolset for each of one or two MCP servers')
    for server in servers:
        validate_server(server)
    if len({s['name'] for s in servers}) != len(servers):
        raise FileProblem(400, 'Duplicate MCP server name')
    if any(not isinstance(t, dict) or t.get('type') != 'mcp_toolset' for t in sets):
        raise FileProblem(400, 'Declare mcp_toolset tools')
    if {t.get('mcp_server_name') for t in sets} != {s['name'] for s in servers}:
        raise FileProblem(400, 'MCP toolsets must match server names')
    for settings in sets:
        configured_tools([], settings)
    params = {k: copy.deepcopy(fields[k]) for k in ('max_tokens', 'messages', 'system') if k in fields}
    params.update(model='webcc-prompt-v1', stream=False)
    bounded_json(params)
    return servers, sets, params


def history(messages, mapping):
    result = copy.deepcopy(messages)
    for message in result:
        blocks = message.get('content')
        if not isinstance(blocks, list):
            continue
        cleaned = []
        for block in blocks:
            if block.get('type') in {'mcp_tool_use', 'mcp_tool_result', 'server_tool_use', 'tool_search_tool_result'}:
                # Server calls and results already executed in the same assistant turn.
                if block['type'] == 'mcp_tool_use':
                    name = alias(block.get('server_name', ''), block.get('name', ''))
                    if name not in mapping:
                        raise FileProblem(400, 'Historical MCP tool is not enabled')
                cleaned.append({'type': 'text', 'text': 'Previous remote MCP data: ' + json.dumps(block, ensure_ascii=False)})
            else:
                cleaned.append(block)
        message['content'] = cleaned
    return result


def directory(servers, sets, connections, on_check, deadline):
    mapping, tools = {}, []
    for server in servers:
        on_check()
        settings = next(t for t in sets if t['mcp_server_name'] == server['name'])
        available = connections.operation(server, timeout=deadline - time.monotonic())
        if set(settings.get('configs', {})) - {t['name'] for t in available}:
            raise FileProblem(400, 'MCP configuration refers to an unknown tool')
        for tool in configured_tools(available, settings):
            name = alias(server['name'], tool['name'])
            mapping[name] = server, tool
            tools.append({'name': name, 'description': server['name'] + '/' + tool['name'] + ': ' + tool['description'],
                          'input_schema': tool['input_schema'], 'strict': True, 'defer_loading': tool['defer_loading']})
    if any(t.get('defer_loading') for t in tools):
        tools.append({'name': 'webcc_tool_search', 'description': 'Discover deferred MCP tools by capability or server/tool name before calling them.',
                      'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 256}},
                                       'required': ['query'], 'additionalProperties': False}})
    if not tools or len(tools) > 32:
        raise FileProblem(400, 'Enable between one and 32 MCP tools')
    return mapping, tools


def describe_call(block, mapping):
    if block['name'] == 'webcc_tool_search':
        return {**block, 'type': 'server_tool_use', 'name': 'tool_search'}
    server, tool = mapping[block['name']]
    return {**block, 'type': 'mcp_tool_use', 'name': tool['name'], 'server_name': server['name']}


def call_tool(block, mapping, tools, connections, deadline):
    if block['name'] == 'webcc_tool_search':
        from web_tools.discovery import search
        found = search({'tools': tools, 'query': block['input']['query'], 'limit': 8})
        call = {**block, 'type': 'server_tool_use', 'name': 'tool_search'}
        result = {'is_error': False, 'content': found['tool_references']}
        external = {'type': 'tool_search_tool_result', 'tool_use_id': block['id'], 'content': found['tool_references']}
    else:
        server, tool = mapping[block['name']]
        call = {**block, 'type': 'mcp_tool_use', 'name': tool['name'], 'server_name': server['name']}
        result = connections.operation(server, tool['name'], block['input'], timeout=deadline - time.monotonic())
        external = {'type': 'mcp_tool_result', 'tool_use_id': block['id'], **result}
    return call, external, {'type': 'tool_result', 'tool_use_id': block['id'], **result}


def execute(handler, fields, on_check, emit=None):
    with Connections() as connections:
        return execute_connected(handler, fields, on_check, connections, emit)


def execute_connected(handler, fields, on_check, connections, emit=None):
    servers, sets, params = prepare_request(fields)
    deadline = time.monotonic() + min(140, handler.manager.request_seconds)
    mapping, tools = directory(servers, sets, connections, on_check, deadline)
    params.update(tools=tools, messages=history(params.get('messages', []), mapping))
    prepare(json.dumps(params).encode())
    output, failed, calls = [], set(), 0
    message = None
    for turn in range(4):
        on_check()
        if time.monotonic() >= deadline:
            raise FileProblem(504, 'MCP request deadline exceeded')
        if turn == 3 or calls >= 8:
            params['tool_choice'] = {'type': 'none'}
        message = infer(handler.manager, handler.caller_key or 'platform', params, 'prompt-v1', deadline, on_check)
        pending = [b for b in message['content'] if b['type'] == 'tool_use']
        if not pending:
            output.extend(message['content'])
            if emit:
                for block in message['content']:
                    emit(block)
            break
        if turn == 3 or calls + len(pending) > 8:
            raise FileProblem(429, 'MCP execution budget reached; no further tools executed')
        results = []
        for block in message['content']:
            if block['type'] != 'tool_use':
                output.append(block)
                if emit:
                    emit(block)
                continue
            on_check()
            if time.monotonic() >= deadline:
                raise FileProblem(504, 'MCP request deadline exceeded')
            if emit:
                emit(describe_call(block, mapping))
            signature = (block['name'], json.dumps(block['input'], sort_keys=True))
            try:
                if signature in failed:
                    raise FileProblem(502, 'Previous execution failed')
                call, external, internal = call_tool(block, mapping, tools, connections, deadline)
            except FileProblem:
                if block['name'] not in mapping:
                    raise
                server, tool = mapping[block['name']]
                call = {**block, 'type': 'mcp_tool_use', 'name': tool['name'], 'server_name': server['name']}
                result = {'is_error': True, 'content': [{'type': 'text', 'text': 'Remote operation failed; execution status may be unknown. It was not retried.'}]}
                external = {'type': 'mcp_tool_result', 'tool_use_id': block['id'], **result}
                internal = {'type': 'tool_result', 'tool_use_id': block['id'], **result}
            if internal['is_error']:
                failed.add(signature)
            calls += 1
            output.extend([call, external])
            if emit:
                emit(external)
            results.append(internal)
        bounded_json(output)
        params['messages'].extend([{'role': 'assistant', 'content': message['content']}, {'role': 'user', 'content': results}])
    return {**message, 'id': 'msg_' + uuid4().hex, 'model': MODEL, 'content': output}


def messages(handler):
    handler.authenticate(scope='mcp')
    if handler.path != '/v1/messages' or handler.headers.get('anthropic-beta') not in (None, 'mcp-client-2025-11-20'):
        raise FileProblem(400, 'Unsupported MCP query or beta header')
    if not handler.manager.web_tools_enabled:
        raise FileProblem(503, 'Web tool adapter is disabled')
    fields = load_json(handler.body(131072))
    if isinstance(fields, dict) and ('container' in fields or 'max_iterations' in fields):
        from mcp_tasks import messages as durable_messages
        durable_messages(handler, fields)
        return
    prepare_request(fields)
    if not SLOTS.acquire(blocking=False):
        raise FileProblem(429, 'MCP Messages requests are busy')
    handler.adapter = 'mcp-v1; schema-validated; web-account; tool-progress'
    watcher = Stream(handler)
    stream = watcher if fields.get('stream') else None
    try:
        if stream:
            stream.start()
            stream.begin({'id': 'msg_' + uuid4().hex, 'type': 'message', 'role': 'assistant', 'model': MODEL,
                          'usage': {'input_tokens': 0, 'output_tokens': 0}})
        result = execute(handler, fields, watcher.check, stream.block if stream else None)
        if stream:
            stream.finish(result['stop_reason'], result.get('usage'))
        else:
            handler.respond(200, result)
    except Exception:
        if stream and stream.started:
            try:
                stream.error('MCP request interrupted; remote operations are not replayed')
            except OSError:
                handler.close_connection = True
        else:
            raise
    finally:
        SLOTS.release()
