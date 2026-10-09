"""Owned runtime resources and the opt-in Messages execution adapter."""
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlsplit
from web_files import FileProblem
from task_store import public_run
from skill_bundles import validate_bundle
from runtime_queue import enqueue
from run_tasks import validate, resume, TERMINAL


def maintain(manager):
    while True:
        time.sleep(30)
        try:
            with manager.cluster.pool.connection() as db:
                db.execute("SELECT pg_advisory_xact_lock(hashtextextended('webcc:runtime-reconcile',0))")
                rows = db.execute('''SELECT id FROM webcc_tasks t WHERE
                    (state IN ('queued','processing','canceling') OR state='waiting' AND expires<%s OR body->>'cleanup_pending'='true')
                    AND NOT EXISTS (SELECT 1 FROM procrastinate_jobs j WHERE j.task_name='webcc.runtime'
                        AND j.args->>'identity'=t.id AND j.status IN ('todo','doing')) LIMIT 100''', (time.time(),)).fetchall()
                for row in rows:
                    enqueue(db, row[0])
        except Exception as error:
            print('runtime_reconcile_failed', type(error).__name__, flush=True)


def internal_token(secret):
    return hmac.new(secret.encode(), b'webcc:runtime:internal:v1', hashlib.sha256).hexdigest()


def process(handler):
    expected = internal_token(handler.manager.admin_key)
    supplied = handler.headers.get('X-WebCC-Internal', '')
    if handler.client_address[0] != '127.0.0.1' or not hmac.compare_digest(expected, supplied):
        raise FileProblem(404, 'Route not found')
    handler.authenticate(admin=True)
    fields = json.loads(handler.body(1024))
    if not isinstance(fields, dict) or set(fields) != {'id'} or not isinstance(fields['id'], str):
        raise FileProblem(400, 'Invalid task dispatch')
    manager = handler.manager
    if not manager.tasks:
        raise FileProblem(503, 'Runtime is disabled')
    item = manager.tasks.get(fields['id'])
    from runtime_forward import infer
    if item['kind'] == 'batch':
        from batch_tasks import process as batch_process
        batch_process(manager, item['id'], infer)
    else:
        from e2b_runtime import E2BRuntime
        from run_tasks import process as run_process
        run_process(manager, item['id'], infer, E2BRuntime())
    handler.respond(200, {'ok': True})


def check_permissions(handler, fields):
    if not isinstance(fields, dict):
        raise FileProblem(400, 'Runtime request must be an object')
    if handler.caller_key:
        scopes = set(handler.manager.api_keys.active(handler.caller_key)['scopes'])
        needed = {'runs'}
        if 'messages' in fields:
            needed |= {'messages', 'experimental_tools'}
        if fields.get('skills'):
            needed.add('skills')
        if fields.get('files') or fields.get('outputs') or 'messages' in fields:
            needed.add('files')
        if not needed.issubset(scopes):
            raise FileProblem(403, 'Key is missing a required runtime permission')


def route(handler, target):
    kind = 'skills' if target.path.startswith('/v1/skills') else 'runs'
    handler.authenticate(scope=kind)
    handler.adapter = 'managed-skills-v1' if kind == 'skills' else 'e2b-runtime-v1'
    manager, owner = handler.manager, handler.caller_key or 'platform'
    if not manager.tasks:
        raise FileProblem(503, 'Runtime requires PostgreSQL and the runtime feature flag')
    if kind == 'skills':
        skill_route(handler, target, owner)
        return
    base = '/v1/runs'
    if target.path == base:
        if handler.command == 'POST':
            if target.query:
                raise FileProblem(400, 'Run creation does not accept query parameters')
            fields = json.loads(handler.body(1048576))
            check_permissions(handler, fields)
            from e2b_runtime import E2BRuntime
            try:
                E2BRuntime()
            except ValueError:
                raise FileProblem(503, 'Sandbox provider is not configured') from None
            body = validate(fields, manager, owner)
            item = manager.tasks.create('run', owner, body, enqueue, body['timeout_seconds'])
            handler.respond(202, public_run(item))
        elif handler.command == 'GET':
            items, more = manager.tasks.list(owner, 'run', target.query)
            handler.respond(200, {'data': [public_run(i) for i in items], 'has_more': more})
        else:
            raise FileProblem(405, 'Unsupported method')
        return
    match = re.fullmatch(base + r'/(run_webcc_[a-f0-9]{32})(?:/(cancel|tool_results))?', target.path)
    if not match or target.query:
        raise FileProblem(404, 'Run route not found')
    identity, action = match.groups()
    item = manager.tasks.get(identity, owner)
    if action == 'cancel' and handler.command == 'POST':
        manager.tasks.cancel(identity, owner)
        with manager.tasks.transaction(identity, owner) as (db, _):
            enqueue(db, identity)
        handler.respond(202, public_run(manager.tasks.get(identity, owner)))
    elif action == 'tool_results' and handler.command == 'POST':
        handler.respond(202, public_run(resume(manager.tasks, identity, owner, json.loads(handler.body(524288)))))
    elif not action and handler.command == 'GET':
        handler.respond(200, public_run(item))
    elif not action and handler.command == 'DELETE':
        handler.respond(200, manager.tasks.delete(identity, owner))
    else:
        raise FileProblem(405, 'Unsupported method')


def skill_route(handler, target, owner):
    store, path = handler.manager.tasks, target.path
    if target.query:
        raise FileProblem(400, 'Skills do not accept query parameters')
    if path == '/v1/skills':
        if handler.command == 'POST':
            handler.respond(201, store.put_skill(owner, validate_bundle(json.loads(handler.body(524288)))))
        elif handler.command == 'GET':
            handler.respond(200, {'data': store.skills(owner), 'has_more': False})
        else:
            raise FileProblem(405, 'Unsupported method')
        return
    match = re.fullmatch(r'/v1/skills/(skill_webcc_[a-f0-9]{32})(?:/versions(?:/([a-f0-9]{24}))?)?', path)
    if not match:
        raise FileProblem(404, 'Skill route not found')
    identity, version = match.groups()
    existing = store.skills(owner, identity)
    if not existing:
        raise FileProblem(404, 'Skill not found')
    if handler.command == 'GET':
        handler.respond(200, store.skill_version(owner, identity, version) if version else {'data': existing, 'has_more': False})
    elif handler.command == 'POST' and path.endswith('/versions'):
        handler.respond(201, store.put_skill(owner, validate_bundle(json.loads(handler.body(524288))), identity))
    elif handler.command == 'DELETE':
        handler.respond(200, store.delete_skill(owner, identity, version))
    else:
        raise FileProblem(405, 'Unsupported method')


def messages(handler):
    if handler.headers.get('X-WebCC-Runtime') != 'e2b-v1' or handler.headers.get('X-WebCC-Tools') or handler.headers.get('anthropic-beta') or handler.path != '/v1/messages':
        raise FileProblem(400, 'Use X-WebCC-Runtime: e2b-v1 without other adapter or beta headers')
    manager, owner = handler.manager, handler.caller_key or 'platform'
    if not manager.tasks:
        raise FileProblem(503, 'Runtime is disabled')
    handler.adapter = 'e2b-runtime-v1; web-account; buffered'
    fields = json.loads(handler.body(1048576))
    if not isinstance(fields, dict) or fields.get('model') != 'webcc-runtime-v1' or fields.get('stream', False) is not False or set(fields) - {'model', 'messages', 'tools', 'container', 'max_tokens', 'stream'}:
        raise FileProblem(400, 'Use webcc-runtime-v1 with stream=false and supported runtime fields')
    container = fields.get('container')
    if isinstance(container, str):
        check_permissions(handler, {'messages': []})
        item = manager.tasks.get(container, owner)
        history = fields.get('messages', [])
        if not isinstance(history, list) or not history or not isinstance(history[-1], dict) or history[-1].get('role') != 'user':
            raise FileProblem(400, 'Resume requires a final user message')
        blocks = history[-1].get('content', [])
        results = [b for b in blocks if isinstance(b, dict) and b.get('type') == 'tool_result'] if isinstance(blocks, list) else []
        if results:
            item = resume(manager.tasks, container, owner, {'tool_results': [{k: b[k] for k in ('tool_use_id', 'content', 'is_error') if k in b} for b in results]})
    else:
        skills = []
        if container is not None:
            if not isinstance(container, dict) or set(container) != {'skills'}:
                raise FileProblem(400, 'Container requires a run ID or explicit skills')
            skills = container['skills']
        tools = fields.get('tools', [])
        if not isinstance(tools, list) or sum(t.get('name') == 'code_execution' for t in tools if isinstance(t, dict)) != 1:
            raise FileProblem(400, 'Declare one code_execution server tool')
        server = next(t for t in tools if t.get('name') == 'code_execution')
        if set(server) != {'name', 'type'} or server['type'] not in {'code_execution_20260120', 'code_execution_20260521'}:
            raise FileProblem(400, 'Invalid code_execution tool')
        request = {'messages': fields.get('messages'), 'max_tokens': fields.get('max_tokens', 4096),
                   'tools': [t for t in tools if t['name'] != 'code_execution'], 'skills': skills,
                   'code_execution_type': server['type']}
        check_permissions(handler, request)
        body = validate(request, manager, owner)
        item = manager.tasks.create('run', owner, body, enqueue, body['timeout_seconds'])
    deadline = time.monotonic() + min(140, manager.request_seconds)
    while item['state'] not in TERMINAL | {'waiting'} and time.monotonic() < deadline:
        handler.check_caller()
        time.sleep(.2)
        item = manager.tasks.get(item['id'], owner)
    if item['state'] not in TERMINAL | {'waiting'}:
        handler.respond(202, public_run(item))
        return
    if item['state'] != 'ended' and item['state'] != 'waiting':
        handler.respond(502, {'error': {'type': 'api_error', 'message': 'Runtime task ' + item['state']}, 'container': {'id': item['id']}, 'details': item.get('error')})
        return
    tool_id = 'srvtoolu_' + item['id'].removeprefix('run_webcc_')
    content = [{'type': 'server_tool_use', 'id': tool_id, 'name': 'code_execution', 'input': {'code': item.get('code', '')}}]
    if item['state'] == 'waiting':
        content.extend(item['pending_tools'])
    else:
        result = item['result']
        content.append({'type': 'code_execution_tool_result', 'tool_use_id': tool_id,
                        'content': {'type': 'code_execution_result', 'stdout': result['stdout'], 'stderr': result['stderr'], 'return_code': 0,
                                    'content': [{'type': 'code_execution_output', 'file_id': f['id']} for f in item.get('output_files', [])]}})
        content.append({'type': 'text', 'text': result['stdout'].strip() or 'Execution completed.'})
    handler.respond(200, {'id': 'msg_' + item['id'], 'type': 'message', 'role': 'assistant', 'model': 'webcc-runtime-v1',
                         'container': {'id': item['id'], 'expires_at': __import__('web_files').stamp(item['expires'])},
                         'content': content, 'stop_reason': 'tool_use' if item['state'] == 'waiting' else 'end_turn', 'stop_sequence': None})
