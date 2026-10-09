"""Durable Messages batches; each item is claimed once before inference."""
import copy
import json
import re
import time
from urllib.parse import parse_qs, urlencode
from web_files import FileProblem
from runtime_queue import enqueue
from task_store import public_batch


def validate_requests(fields, manager, owner, mode):
    if not isinstance(fields, dict) or set(fields) != {'requests'}:
        raise FileProblem(400, 'Batch requires requests')
    requests = fields['requests']
    if not isinstance(requests, list) or not 1 <= len(requests) <= 100:
        raise FileProblem(400, 'Batch supports 1 to 100 requests')
    result, seen, expanded_size = [], set(), 0
    for request in requests:
        if not isinstance(request, dict) or set(request) != {'custom_id', 'params'}:
            raise FileProblem(400, 'Each request requires custom_id and params')
        identity, params = request['custom_id'], request['params']
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', identity) or identity in seen:
            raise FileProblem(400, 'custom_id must be unique and contain 1 to 64 safe characters')
        seen.add(identity)
        if not isinstance(params, dict) or params.get('stream', False) is not False or not isinstance(params.get('model'), str):
            raise FileProblem(400, 'Batch requests require model and stream=false')
        if type(params.get('max_tokens')) is not int or not 1 <= params['max_tokens'] <= 8192 or not isinstance(params.get('messages'), list) or not params['messages']:
            raise FileProblem(400, 'Invalid batch Messages parameters')
        if set(params) - {'model', 'messages', 'max_tokens', 'system', 'temperature', 'top_p', 'top_k', 'stop_sequences', 'stream', 'tools', 'tool_choice', 'output_config'}:
            raise FileProblem(400, 'Unsupported batch parameter')
        if not mode and ({'tools', 'tool_choice', 'output_config'} & params.keys()):
            raise FileProblem(400, 'Batch tools require X-WebCC-Tools: prompt-v1')
        params = copy.deepcopy(params)
        params['stream'] = False
        params, _ = manager.files.resolve(owner, params)
        expanded_size += len(json.dumps(params, ensure_ascii=False).encode())
        if expanded_size > 8388608:
            raise FileProblem(413, 'Expanded batch input exceeds 8 MiB')
        if mode:
            from web_tools.api import prepare
            try:
                prepare(json.dumps(params).encode())
            except FileProblem:
                raise
            except Exception:
                raise FileProblem(400, 'Invalid batch tool parameters') from None
        result.append({'custom_id': identity, 'params': params, 'state': 'queued'})
    return result


def process(manager, identity, inference):
    store = manager.tasks
    with store.execution(identity) as held:
        if not held:
            return
        item = store.get(identity)
        if item['state'] == 'ended':
            return
        # One item per queue job lets sandbox continuations and cancellations progress.
        index = next((i for i, r in enumerate(item['requests']) if 'result' not in r), None)
        if index is None:
            return
        with store.transaction(identity) as (_, item):
            request = item['requests'][index]
            if request['state'] == 'processing':
                request['result'] = {'type': 'errored', 'error': {'type': 'error', 'error': {
                    'type': 'api_error', 'message': 'Previous execution outcome is unknown; item was not replayed'}}}
            elif item.get('cancel_requested') or item['expires'] <= time.time():
                request['result'] = {'type': 'canceled' if item.get('cancel_requested') else 'expired'}
            else:
                request['state'] = 'processing'
                item['state'] = 'processing'
        item = store.get(identity)
        if 'result' not in item['requests'][index]:
            try:
                message = inference(manager, item['owner'], item['requests'][index]['params'], item.get('mode'))
                result = {'type': 'succeeded', 'message': message}
            except FileProblem as error:
                result = {'type': 'errored', 'error': {'type': 'error', 'error': {'type': 'api_error', 'message': str(error)}}}
            except Exception:
                result = {'type': 'errored', 'error': {'type': 'error', 'error': {'type': 'api_error', 'message': 'Execution outcome unknown; not replayed'}}}
            with store.transaction(identity) as (_, item):
                item['requests'][index]['result'] = result
                item['requests'][index]['state'] = 'ended'
                item['requests'][index].pop('params', None)
        with store.transaction(identity) as (db, item):
            if all('result' in r for r in item['requests']):
                item['state'], item['ended_at'] = 'ended', time.time()
            else:
                enqueue(db, identity)


def route(handler, target):
    handler.authenticate(scope='batches')
    handler.adapter = 'managed-batches-v1'
    manager, owner = handler.manager, handler.caller_key or 'platform'
    if not manager.tasks:
        raise FileProblem(503, 'Batches require the PostgreSQL runtime')
    base = '/v1/messages/batches'
    path = target.path
    params = parse_qs(target.query, keep_blank_values=True, max_num_fields=5)
    if 'beta' in params:
        if params.pop('beta') != ['true']:
            raise FileProblem(400, 'Invalid beta selector')
        target = target._replace(query=urlencode(params, doseq=True))
    if path == base:
        if handler.command == 'POST':
            if target.query or handler.headers.get('anthropic-beta'):
                raise FileProblem(400, 'Batch creation does not accept query or beta extensions')
            mode = handler.headers.get('X-WebCC-Tools')
            if mode not in (None, 'prompt-v1') or mode and not manager.web_tools_enabled:
                raise FileProblem(400, 'Invalid batch tool mode')
            if handler.caller_key and 'messages' not in manager.api_keys.active(owner)['scopes']:
                raise FileProblem(403, 'Batches require messages permission')
            requests = validate_requests(json.loads(handler.body(1048576)), manager, owner, mode)
            item = manager.tasks.create('batch', owner, {'requests': requests, 'mode': mode}, enqueue)
            handler.respond(200, public_batch(item))
        elif handler.command == 'GET':
            items, more = manager.tasks.list(owner, 'batch', target.query)
            data = [public_batch(i) for i in items]
            handler.respond(200, {'data': data, 'has_more': more, 'first_id': data[0]['id'] if data else None, 'last_id': data[-1]['id'] if data else None})
        else:
            raise FileProblem(405, 'Unsupported method')
        return
    match = re.fullmatch(base + r'/(msgbatch_[a-f0-9]{32})(?:/(cancel|results))?', path)
    if not match or target.query:
        raise FileProblem(404, 'Batch route not found')
    identity, action = match.groups()
    item = manager.tasks.get(identity, owner)
    if action == 'cancel' and handler.command == 'POST':
        item = manager.tasks.cancel(identity, owner)
    elif action == 'results' and handler.command == 'GET':
        if item['state'] != 'ended':
            raise FileProblem(409, 'Batch is still processing')
        raw = b''.join((json.dumps({'custom_id': r['custom_id'], 'result': r['result']}, ensure_ascii=False) + '\n').encode() for r in item['requests'])
        handler.respond(200, raw, 'application/x-jsonl')
        return
    elif not action and handler.command == 'DELETE':
        manager.tasks.delete(identity, owner)
        handler.respond(200, {'id': identity, 'type': 'message_batch_deleted'})
        return
    elif action or handler.command != 'GET':
        raise FileProblem(405, 'Unsupported method')
    handler.respond(200, public_batch(item))
