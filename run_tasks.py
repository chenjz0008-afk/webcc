"""Durable execution state machine shared by code execution and Skills."""
import copy
import json
import mimetypes
import keyword
import re
import time
from jsonschema import Draft202012Validator
from web_files import FileProblem
from web_tools import check_schema
from skill_bundles import safe_path, snapshot
from runtime_queue import enqueue

TERMINAL = {'ended', 'failed', 'canceled', 'expired', 'unknown'}


def validate(fields, manager, owner):
    allowed = {'code', 'messages', 'max_tokens', 'tools', 'skills', 'files', 'outputs', 'timeout_seconds', 'code_execution_type'}
    if not isinstance(fields, dict) or set(fields) - allowed or ('code' in fields) == ('messages' in fields):
        raise FileProblem(400, 'Provide code or messages, but not both')
    code = fields.get('code')
    if code is not None and (not isinstance(code, str) or not code.strip() or len(code.encode()) > 65536):
        raise FileProblem(400, 'Code must be nonempty Python up to 64 KiB')
    if 'messages' in fields and (not isinstance(fields['messages'], list) or not fields['messages']):
        raise FileProblem(400, 'Messages must be a nonempty list')
    if type(fields.get('max_tokens', 4096)) is not int or not 16 <= fields.get('max_tokens', 4096) <= 8192:
        raise FileProblem(400, 'max_tokens must be 16 to 8192')
    seconds = fields.get('timeout_seconds', 300)
    if type(seconds) is not int or not 30 <= seconds <= 600:
        raise FileProblem(400, 'Runtime timeout must be 30 to 600 seconds')
    caller_type = fields.get('code_execution_type', 'code_execution_20260120')
    if caller_type not in {'code_execution_20260120', 'code_execution_20260521'}:
        raise FileProblem(400, 'Invalid code execution type')
    tools = fields.get('tools', [])
    if not isinstance(tools, list) or len(tools) > 32:
        raise FileProblem(400, 'Use at most 32 client tools')
    seen = set()
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) - {'name', 'description', 'input_schema', 'allowed_callers', 'strict'}:
            raise FileProblem(400, 'Invalid runtime client tool')
        name = tool.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,63}', name) or keyword.iskeyword(name) or name in seen or name in {'print', 'exec', 'eval', 'open', 'input', '__name__'}:
            raise FileProblem(400, 'Tool names must be unique Python identifiers')
        seen.add(name)
        if not isinstance(tool.get('input_schema'), dict) or tool['input_schema'].get('type') != 'object':
            raise FileProblem(400, 'Client tool requires an object schema')
        try:
            check_schema(tool['input_schema'])
        except Exception:
            raise FileProblem(400, 'Invalid client tool schema') from None
        callers = tool.get('allowed_callers', ['code_execution_20260120'])
        if not isinstance(callers, list) or not callers or set(callers) - {'direct', 'code_execution_20260120', 'code_execution_20260521'} or caller_type not in callers:
            raise FileProblem(400, 'Runtime tools must allow a code execution caller')
        if 'strict' in tool and type(tool['strict']) is not bool:
            raise FileProblem(400, 'strict must be boolean')
    outputs = fields.get('outputs', [])
    if not isinstance(outputs, list) or any(not isinstance(v, str) for v in outputs) or len(outputs) > 8 or len(set(outputs)) != len(outputs):
        raise FileProblem(400, 'Use at most eight unique output paths')
    for path in outputs:
        safe_path(path)
    inputs = fields.get('files', [])
    if not isinstance(inputs, list) or len(inputs) > 8:
        raise FileProblem(400, 'Use at most eight input files')
    names, input_bytes = set(), 0
    for file in inputs:
        if not isinstance(file, dict) or set(file) != {'file_id', 'path'}:
            raise FileProblem(400, 'Input files require file_id and path')
        safe_path(file['path'])
        if file['path'] in names:
            raise FileProblem(400, 'Duplicate input path')
        names.add(file['path'])
        input_bytes += len(manager.files.get(owner, file['file_id'])[6])
    if input_bytes > 20971520:
        raise FileProblem(413, 'Combined sandbox input files exceed 20 MiB')
    return {**copy.deepcopy(fields), 'code_execution_type': caller_type, 'tools': copy.deepcopy(tools), 'outputs': outputs, 'files': inputs,
            'skill_snapshots': snapshot(manager.tasks, owner, fields.get('skills', [])), 'timeout_seconds': seconds,
            'pending_tools': [], 'delivered': {}, 'result': None, 'error': None, 'artifact_files': []}


def plan(manager, item, inference):
    descriptions = [{k: t.get(k) for k in ('name', 'description', 'input_schema')} for t in item['tools']]
    instruction = ('Write a Python program for the requested task. It will run in an isolated sandbox. '
        'Client tools are async functions named as declared, take one dict, return a string; use top-level await or asyncio.gather. '
        'Do not execute website tools. Do not invent tool results. Files are under input/. '
        'Selected Skills are under skills/<name>/; read resources when needed. '
        'Print the final answer or execution summary. Write only requested artifacts under output/. '
        'Return execute_python with code and outputs. The executor will really run it. Client tool definitions: ' + json.dumps(descriptions))
    for skill in item['skill_snapshots']:
        instruction += '\nSkill ' + skill['name'] + ': ' + skill['instructions']
    instruction += '\nInput paths: ' + json.dumps(item.get('files', []))
    tool = {'name': 'execute_python', 'description': 'Execute the program in the sandbox', 'input_schema': {
        'type': 'object', 'properties': {'code': {'type': 'string'}, 'outputs': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 8}},
        'required': ['code', 'outputs'], 'additionalProperties': False}}
    request = {'model': 'webcc-prompt-v1', 'max_tokens': min(8192, item.get('max_tokens', 4096)),
               'messages': item['messages'], 'system': instruction, 'tools': [tool],
               'tool_choice': {'type': 'tool', 'name': 'execute_python', 'disable_parallel_tool_use': True}}
    message = inference(manager, item['owner'], request, 'prompt-v1')
    calls = [b for b in message['content'] if b['type'] == 'tool_use']
    if len(calls) != 1 or calls[0]['name'] != 'execute_python':
        raise FileProblem(502, 'Model did not produce a valid execution plan')
    code, outputs = calls[0]['input']['code'], calls[0]['input']['outputs']
    if not code.strip() or len(code.encode()) > 65536 or len(set(outputs)) != len(outputs):
        raise FileProblem(502, 'Model execution plan exceeds limits')
    for path in outputs:
        safe_path(path)
    return code, outputs


def resume(store, identity, owner, fields):
    if not isinstance(fields, dict) or set(fields) != {'tool_results'} or not isinstance(fields['tool_results'], list) or not fields['tool_results']:
        raise FileProblem(400, 'Provide tool_results')
    with store.transaction(identity, owner) as (db, item):
        if item['state'] != 'waiting' or item.get('cancel_requested') or item['expires'] <= time.time():
            raise FileProblem(409, 'Run is not waiting for tool results')
        pending = {t['id'] for t in item['pending_tools']}
        supplied = set()
        for result in fields['tool_results']:
            if not isinstance(result, dict) or set(result) - {'tool_use_id', 'content', 'is_error'}:
                raise FileProblem(400, 'Invalid tool result')
            identity_ = result.get('tool_use_id')
            if identity_ not in pending or identity_ in supplied or identity_ in item['delivered']:
                raise FileProblem(409, 'Unknown or duplicate tool result')
            content = result.get('content')
            if not isinstance(content, str) or len(content.encode()) > 65536 or type(result.get('is_error', False)) is not bool:
                raise FileProblem(400, 'Tool result requires text up to 64 KiB and a boolean is_error')
            supplied.add(identity_)
            item['delivered'][identity_] = {'content': content, 'is_error': result.get('is_error', False), 'written': False}
        item['state'] = 'processing'
        enqueue(db, item['id'])
    return store.get(identity, owner)


def clean(manager, item, provider):
    if not item.get('sandbox_id'):
        if item.get('phase') == 'creating':
            try:
                provider.reconcile(item['id'])
                # A timed-out create could still finish after the first list response.
                return time.time() >= item['expires'] + 30
            except Exception:
                return False
        return True
    try:
        provider.kill(item['sandbox_id'])
        return True
    except Exception as error:
        from e2b.exceptions import NotFoundException
        return isinstance(error, NotFoundException)


def process(manager, identity, inference, provider):
    store = manager.tasks
    with store.execution(identity) as held:
        if not held:
            return
        item = store.get(identity)
        try:
            if item['state'] in TERMINAL:
                if item.get('cleanup_pending'):
                    cleaned = clean(manager, item, provider)
                    with store.transaction(identity) as (db, current):
                        current['cleanup_pending'] = not cleaned
                        if current['cleanup_pending']:
                            enqueue(db, identity, 30)
                return
            if item['owner'] != 'platform':
                record = manager.api_keys.active(item['owner'])
                needed = {'runs'} | ({'files'} if item.get('files') or item.get('outputs') or item.get('messages') else set()) | ({'skills'} if item.get('skill_snapshots') else set())
                if not needed.issubset(record['scopes']):
                    raise FileProblem(403, 'Run owner no longer has required permissions')
            if item.get('cancel_requested') or item['expires'] <= time.time():
                cleaned = clean(manager, item, provider)
                with store.transaction(identity) as (db, current):
                    current['state'] = 'canceled' if item.get('cancel_requested') else 'expired'
                    current['pending_tools'] = []
                    current['cleanup_pending'] = not cleaned
                    if current['cleanup_pending']:
                        enqueue(db, identity, 30)
                return
            if not item.get('sandbox_id'):
                if item['state'] == 'processing':
                    raise FileProblem(409, 'Previous sandbox creation outcome unknown; execution was not replayed')
                with store.transaction(identity) as (db, current):
                    db.execute("SELECT pg_advisory_xact_lock(hashtextextended('webcc:sandbox-capacity',0))")
                    count = db.execute("SELECT count(*) FROM webcc_tasks WHERE kind='run' AND (state IN ('processing','waiting','canceling') OR body->>'cleanup_pending'='true') AND body ? 'sandbox_slot'").fetchone()[0]
                    if count >= 2:
                        enqueue(db, identity, 5)
                        return
                    current.update(state='processing', sandbox_slot=True, phase='planning' if 'code' not in item else 'creating')
                if 'code' not in item:
                    code, outputs = plan(manager, item, inference)
                    item.update(code=code, outputs=outputs)
                    with store.transaction(identity) as (_, current):
                        current.update(phase='creating', expires=time.time() + item['timeout_seconds'])
                seconds = item['timeout_seconds']
                sandbox = provider.create(identity, seconds)
                with store.transaction(identity) as (_, current):
                    current.update(sandbox_id=sandbox.sandbox_id, expires=time.time() + seconds,
                                   code=item['code'], outputs=item['outputs'], phase='starting')
                item = store.get(identity)
                if item.get('cancel_requested'):
                    enqueue_after(store, identity)
                    return
                inputs = [(v['path'], bytes(manager.files.get(item['owner'], v['file_id'])[6])) for v in item.get('files', [])]
                pid = provider.start(sandbox, item['code'], item['tools'], seconds, item['skill_snapshots'], inputs)
                with store.transaction(identity) as (_, current):
                    current.update(pid=pid, phase='running')
                item = store.get(identity)
            else:
                if item.get('phase') in {'starting', 'finishing'}:
                    raise FileProblem(409, 'Command start outcome unknown; program was not replayed')
                if item['state'] == 'waiting':
                    enqueue_after(store, identity, max(1, item['expires'] - time.time()))
                    return
                sandbox = provider.connect(item['sandbox_id'], max(1, int(item['expires'] - time.time())))
            for call_id, value in item['delivered'].items():
                if not value['written']:
                    provider.deliver(sandbox, call_id, {k: value[k] for k in ('content', 'is_error')})
                    with store.transaction(identity) as (_, current):
                        current['delivered'][call_id]['written'] = True
            done = provider.read_json(sandbox, 'done.json')
            if done is not None:
                if not isinstance(done, dict) or set(done) != {'stdout', 'stderr', 'error'} or any(not isinstance(done[k], str) or len(done[k]) > 16384 for k in ('stdout', 'stderr')):
                    raise FileProblem(502, 'Invalid sandbox execution result')
                with store.transaction(identity) as (_, current):
                    current['phase'] = 'finishing'
                files = []
                if not done['error']:
                    for path in item['outputs']:
                        raw = provider.output(sandbox, path)
                        mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
                        if mime in {'text/csv', 'text/markdown', 'application/json'}:
                            mime = 'text/plain'
                        file = manager.files.put(item['owner'], path.rsplit('/', 1)[-1], mime, raw, 86400, generated=True)
                        files.append(file)
                        with store.transaction(identity) as (_, current):
                            current['artifact_files'].append(file['id'])
                cleaned = clean(manager, item, provider)
                with store.transaction(identity) as (db, current):
                    current.update(state='failed' if done['error'] else 'ended', result=done,
                                   error=done['error'], output_files=files, pending_tools=[], cleanup_pending=not cleaned)
                    if not cleaned:
                        enqueue(db, identity, 30)
                return
            calls = provider.pending(sandbox)
            tools = {t['name']: t for t in item['tools']}
            pending, seen = [], set()
            for call in calls:
                if not isinstance(call, dict) or set(call) != {'id', 'name', 'input'} or not isinstance(call['id'], str) or not re.fullmatch(r'toolu_[a-f0-9]{32}', call['id']) or call['id'] in seen:
                    raise FileProblem(502, 'Invalid sandbox tool event')
                seen.add(call['id'])
                if call['name'] not in tools or not isinstance(call['input'], dict):
                    raise FileProblem(502, 'Sandbox requested an undeclared tool')
                Draft202012Validator(tools[call['name']]['input_schema']).validate(call['input'])
                if call['id'] not in item['delivered']:
                    pending.append({'type': 'tool_use', **call, 'caller': {'type': item.get('code_execution_type', 'code_execution_20260120'), 'tool_id': 'srvtoolu_' + identity.removeprefix('run_webcc_')}})
            if pending:
                provider.pause(sandbox)
                with store.transaction(identity) as (db, current):
                    current.update(state='waiting', pending_tools=pending)
                    enqueue(db, identity, max(1, current['expires'] - time.time()))
            else:
                enqueue_after(store, identity, 1)
        except Exception as error:
            item = store.get(identity)
            cleaned = clean(manager, item, provider)
            for file_id in item.get('artifact_files', []):
                try:
                    manager.files.delete(item['owner'], file_id)
                except FileProblem:
                    pass
            with store.transaction(identity) as (db, current):
                current.update(state='unknown' if item.get('phase') == 'creating' or isinstance(error, FileProblem) and error.status == 409 else 'failed',
                    error={'type': type(error).__name__, 'message': str(error) if isinstance(error, FileProblem) else 'Sandbox execution failed; program was not replayed'},
                    pending_tools=[], cleanup_pending=not cleaned)
                if not cleaned:
                    enqueue(db, identity, 30)


def enqueue_after(store, identity, delay=0):
    with store.transaction(identity) as (db, _):
        enqueue(db, identity, delay)
