"""Durable MCP checkpoints and dispatch ledger on the existing task queue."""
import base64
import copy
import hashlib
import json
import time
from cryptography.fernet import Fernet
from mcp_connector import Connections
from mcp_messages import prepare_request, directory, history, call_tool, describe_call, MODEL
from runtime_queue import enqueue
from task_store import public_run
from web_files import FileProblem
from web_tools.api import prepare
from web_tools.history import bounded_json


def cipher(manager):
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('webcc:mcp:v1:' + manager.admin_key).encode()).digest()))


def submit(handler, fields):
    if not handler.manager.tasks:
        raise FileProblem(503, 'MCP checkpoints require the PostgreSQL runtime')
    fields = copy.deepcopy(fields)
    rounds = fields.pop('max_iterations', 3)
    if type(rounds) is not int or not 1 <= rounds <= 4:
        raise FileProblem(400, 'max_iterations must be one to four')
    servers, sets, params = prepare_request(fields)
    owner = handler.caller_key or 'platform'
    encoded = cipher(handler.manager).encrypt(json.dumps({'owner': owner, 'servers': servers}).encode()).decode()
    body = {'credentials': encoded, 'toolsets': sets, 'params': params, 'output': [], 'ledger': {}, 'failed_calls': [],
            'turns': 0, 'window_start': 0, 'window_turn': 0, 'round_budget': rounds, 'calls': 0, 'phase': 'planning'}
    return handler.manager.tasks.create('mcp', owner, body, enqueue, 600)


def resume(manager, identity, owner):
    with manager.tasks.transaction(identity, owner) as (db, item):
        if item['kind'] != 'mcp' or item['state'] != 'waiting' or item.get('cancel_requested') or item['expires'] <= time.time():
            raise FileProblem(409, 'MCP session is not resumable')
        item.update(state='queued', window_start=len(item['output']), window_turn=0)
        enqueue(db, identity)
    return manager.tasks.get(identity, owner)


def message(item):
    result = item.get('message') or {'id': 'msg_' + item['id'], 'type': 'message', 'role': 'assistant', 'model': MODEL,
        'stop_reason': 'pause_turn', 'stop_sequence': None, 'usage': item.get('usage') or {'input_tokens': 0, 'output_tokens': 0}}
    return {**result, 'content': item['output'][item.get('window_start', 0):], 'container': {'id': item['id']}}


def process(manager, identity, inference):
    store = manager.tasks
    with store.execution(identity) as held:
        if not held:
            return
        item = store.get(identity)
        if item['kind'] != 'mcp':
            raise FileProblem(400, 'Wrong task kind')
        if item['state'] in {'ended', 'failed', 'unknown', 'canceled', 'expired'}:
            return
        if item.get('cancel_requested') or item['expires'] <= time.time():
            with store.transaction(identity) as (_, current):
                current.update(state='canceled' if item.get('cancel_requested') else 'expired')
                current.pop('credentials', None)
            return
        if item['state'] == 'waiting':
            return
        if item['phase'] == 'dispatching':
            with store.transaction(identity) as (_, current):
                current.update(state='unknown', error={'type': 'api_error', 'message': 'Remote execution outcome unknown; operation was not replayed'})
                current.pop('credentials', None)
            return
        try:
            if item['owner'] != 'platform':
                scopes = set(manager.api_keys.active(item['owner'])['scopes'])
                if not {'mcp', 'messages', 'experimental_tools'} <= scopes:
                    raise FileProblem(403, 'MCP owner permissions are no longer available')
            secret = json.loads(cipher(manager).decrypt(item['credentials'].encode()))
            if secret['owner'] != item['owner']:
                raise FileProblem(403, 'MCP credentials belong to another caller')
            deadline = time.monotonic() + min(120, item['expires'] - time.time())
            def active():
                current = store.get(identity)
                if current.get('cancel_requested') or current['expires'] <= time.time():
                    raise FileProblem(409, 'MCP session canceled or expired')
                if item['owner'] != 'platform':
                    manager.api_keys.check_active(item['owner'])
            with Connections() as connections:
                mapping, tools = directory(secret['servers'], item['toolsets'], connections, active, deadline)
                params = copy.deepcopy(item['params'])
                if 'tools' not in params:
                    params.update(tools=tools, messages=history(params.get('messages', []), mapping))
                    prepare(json.dumps(params).encode())
                # One model turn per job keeps the existing queue fair to sandbox polls.
                if item['phase'] != 'calling':
                    active()
                    with store.transaction(identity) as (_, current):
                        current.update(state='processing', phase='planning')
                    reply = inference(manager, item['owner'], params, 'prompt-v1', deadline, active)
                    pending = [b for b in reply['content'] if b['type'] == 'tool_use']
                    with store.transaction(identity) as (_, current):
                        current.update(plan=reply, internal_results=[], phase='calling', params=params, usage=reply.get('usage'))
                        if not pending:
                            current['output'].extend(reply['content'])
                            current.update(state='ended', message=reply)
                            current.pop('credentials', None)
                            return
                    item = store.get(identity)
                reply = item['plan']
                for block in reply['content']:
                    if block['type'] != 'tool_use':
                        continue
                    if block['id'] in item['ledger']:
                        continue
                    active()
                    if item['calls'] >= 32 or time.monotonic() >= deadline:
                        raise FileProblem(429, 'MCP session execution budget reached')
                    signature = hashlib.sha256(json.dumps([block['name'], block['input']], sort_keys=True).encode()).hexdigest()
                    if signature in item.get('failed_calls', []):
                        raise FileProblem(409, 'Previous remote operation failed; automatic replay prevented')
                    call = describe_call(block, mapping)
                    with store.transaction(identity) as (_, current):
                        current.update(phase='dispatching')
                        current['output'].append(call)
                    # Persist the dispatch marker before any remote side effect.
                    call, external, internal = call_tool(block, mapping, tools, connections, deadline)
                    with store.transaction(identity) as (_, current):
                        current['output'].append(external)
                        current['internal_results'].append(internal)
                        current['ledger'][block['id']] = 'completed'
                        if internal['is_error']:
                            current.setdefault('failed_calls', []).append(signature)
                        current.update(phase='calling', calls=current['calls'] + 1)
                        bounded_json(current['output'])
                    item = store.get(identity)
                params['messages'].extend([{'role': 'assistant', 'content': reply['content']}, {'role': 'user', 'content': item['internal_results']}])
                prepare(json.dumps(params).encode())
                with store.transaction(identity) as (db, current):
                    current.update(params=params, phase='planning', turns=current['turns'] + 1, window_turn=current['window_turn'] + 1)
                    if current['turns'] >= 16:
                        current.update(state='failed', error={'type': 'api_error', 'message': 'MCP turn budget exhausted'})
                        current.pop('credentials', None)
                    elif current['window_turn'] >= current['round_budget']:
                        current['state'] = 'waiting'
                        enqueue(db, identity, max(1, current['expires'] - time.time()))
                    else:
                        current['state'] = 'queued'
                        enqueue(db, identity)
        except Exception as error:
            with store.transaction(identity) as (_, current):
                current.update(state='unknown' if current['phase'] == 'dispatching' else 'canceled' if current.get('cancel_requested') else 'failed',
                    error={'type': 'api_error', 'message': str(error) if isinstance(error, FileProblem) else 'MCP execution failed; remote operations were not replayed'})
                current.pop('credentials', None)


def route(handler, target):
    import re
    handler.authenticate(scope='mcp')
    handler.adapter = 'durable-mcp-v1'
    if not handler.manager.tasks:
        raise FileProblem(503, 'MCP checkpoints require PostgreSQL')
    owner, path = handler.caller_key or 'platform', target.path
    if target.query:
        raise FileProblem(400, 'MCP sessions do not accept query parameters')
    match = re.fullmatch(r'/v1/mcp/sessions/(run_webcc_[a-f0-9]{32})(?:/(resume|cancel))?', path)
    if not match:
        raise FileProblem(404, 'MCP session route not found')
    identity, action = match.groups()
    item = handler.manager.tasks.get(identity, owner)
    if item['kind'] != 'mcp':
        raise FileProblem(404, 'MCP session not found')
    if handler.command == 'GET' and not action:
        handler.respond(200, {**public_run(item), 'message': message(item)})
    elif handler.command == 'POST' and action == 'resume':
        handler.respond(202, public_run(resume(handler.manager, identity, owner)))
    elif handler.command == 'POST' and action == 'cancel':
        handler.manager.tasks.cancel(identity, owner)
        with handler.manager.tasks.transaction(identity, owner) as (db, _):
            enqueue(db, identity)
        handler.respond(202, public_run(handler.manager.tasks.get(identity, owner)))
    elif handler.command == 'DELETE' and not action:
        handler.respond(200, handler.manager.tasks.delete(identity, owner))
    else:
        raise FileProblem(405, 'Unsupported MCP session operation')


def messages(handler, fields):
    from message_stream import Stream
    owner = handler.caller_key or 'platform'
    if not handler.manager.tasks:
        raise FileProblem(503, 'MCP checkpoints require PostgreSQL')
    if fields.get('container'):
        if set(fields) - {'model', 'container', 'messages', 'stream'} or fields.get('model') != MODEL or type(fields.get('stream', False)) is not bool:
            raise FileProblem(400, 'Resume requires model, container and supported fields')
        item = resume(handler.manager, fields['container'], owner)
    else:
        item = submit(handler, fields)
    handler.adapter = 'durable-mcp-v1; tool-progress'
    watcher = Stream(handler)
    stream = watcher if fields.get('stream') else None
    sent, deadline = item.get('window_start', 0), time.monotonic() + min(140, handler.manager.request_seconds)
    try:
        if stream:
            stream.start()
            stream.begin({'id': 'msg_' + item['id'], 'type': 'message', 'role': 'assistant', 'model': MODEL,
                          'container': {'id': item['id']}, 'usage': {'input_tokens': 0, 'output_tokens': 0}})
        while True:
            watcher.check()
            item = handler.manager.tasks.get(item['id'], owner)
            if stream:
                for block in item['output'][sent:]:
                    stream.block(block)
                    sent += 1
            if item['state'] in {'waiting', 'ended', 'failed', 'unknown', 'canceled', 'expired'} or time.monotonic() >= deadline:
                break
            time.sleep(.2)
        if item['state'] in {'failed', 'unknown', 'canceled', 'expired'}:
            if stream:
                stream.error(item.get('error', {}).get('message', 'MCP session ' + item['state']))
            else:
                handler.respond(502, {'error': item.get('error') or {'type': 'api_error', 'message': item['state']}, 'container': {'id': item['id']}})
        else:
            result = message(item)
            if stream:
                stream.finish(result['stop_reason'], result.get('usage'))
            else:
                handler.respond(200, result)
    except Exception:
        try:
            handler.manager.tasks.cancel(item['id'], owner)
            with handler.manager.tasks.transaction(item['id'], owner) as (db, _):
                enqueue(db, item['id'])
        except Exception as error:
            print('mcp_cancel_failed', type(error).__name__, flush=True)
        if stream and stream.started:
            try:
                stream.error('MCP request interrupted; cancellation requested')
            except OSError:
                handler.close_connection = True
        else:
            raise
