"""SDK Skills resource shapes over the existing owned immutable version store."""
import io
import json
import re
import zipfile
from email.parser import BytesParser
from email.policy import default
from urllib.parse import parse_qs
from skill_bundles import safe_path, validate_bundle
from web_files import FileProblem


def upload(content_type, raw):
    if content_type.startswith('application/json'):
        return validate_bundle(json.loads(raw))
    if not content_type.startswith('multipart/form-data;') or '\r' in content_type or '\n' in content_type:
        raise FileProblem(400, 'Skill upload requires JSON or multipart/form-data')
    message = BytesParser(policy=default).parsebytes(('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + raw)
    if not message.is_multipart() or message.defects:
        raise FileProblem(400, 'Invalid skill multipart data')
    files, display, directory = {}, None, None
    for part in message.iter_parts():
        name = part.get_param('name', header='content-disposition')
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes) or part.defects or len(data) > 65536:
            raise FileProblem(400, 'Invalid skill resource')
        try:
            text = data.decode('utf-8')
        except UnicodeError:
            raise FileProblem(400, 'Skill resources must be UTF-8 text') from None
        if name == 'display_name' and display is None:
            display = text
        elif name in {'files', 'files[]'} and len(files) < 64:
            filename = part.get_filename()
            safe_path(filename)
            if '/' not in filename:
                raise FileProblem(400, 'Skill files require a shared top-level directory')
            folder, filename = filename.split('/', 1)
            if directory is not None and directory != folder or filename in files:
                raise FileProblem(400, 'Duplicate resource or inconsistent skill directory')
            directory = folder
            files[filename] = text
        else:
            raise FileProblem(400, 'Unsupported skill upload field')
    bundle = validate_bundle({'files': files})
    if directory != bundle['name']:
        raise FileProblem(400, 'Skill directory must match SKILL.md name')
    if display is not None:
        if not display.strip() or len(display) > 255 or '\n' in display or '\r' in display:
            raise FileProblem(400, 'Invalid skill display_name')
        bundle['display_name'] = display
    return bundle


def skill(rows):
    latest = rows[0]
    return {**latest, 'display_name': latest.get('display_name', latest['name']),
            'latest_version': latest['version'], 'latest_version_id': latest['version'],
            'source': 'custom', 'updated_at': latest['created_at'], 'created_at': rows[-1]['created_at']}


def version(row):
    return {**row, 'id': row['version'], 'type': 'skill_version', 'skill_id': row['id']}


def page(data, params, versions=False):
    try:
        limit = int(params.get('limit', ['20'])[0])
    except ValueError:
        raise FileProblem(400, 'Invalid limit') from None
    if not 1 <= limit <= 1000:
        raise FileProblem(400, 'Skill limit must be one to 1000')
    cursor = params.get('page', [None])[0]
    if cursor is not None:
        identities = [v['id'] for v in data]
        if cursor not in identities:
            raise FileProblem(400, 'Unknown pagination cursor')
        data = data[identities.index(cursor) + 1:]
    chosen = data[:limit]
    return {'data': chosen, 'has_more': len(data) > limit, 'next_page': chosen[-1]['id'] if len(data) > limit else None}


def route(handler, target, owner):
    store = handler.manager.tasks
    params = parse_qs(target.query, keep_blank_values=True, max_num_fields=5)
    if any(len(v) != 1 for v in params.values()) or set(params) - {'beta', 'limit', 'page', 'source'} or params.get('beta', ['true']) != ['true']:
        raise FileProblem(400, 'Unsupported Skill query')
    if handler.headers.get('anthropic-beta') not in (None, 'skills-2025-10-02'):
        raise FileProblem(400, 'Unsupported Skills beta')
    if handler.headers.get('anthropic-workspace-id'):
        raise FileProblem(400, 'Workspace routing is not provided; resources belong to the caller key')
    if handler.command != 'GET' and set(params) - {'beta'}:
        raise FileProblem(400, 'Unsupported Skill mutation query')
    path = target.path
    if path != '/v1/skills' and 'source' in params:
        raise FileProblem(400, 'source applies only to the Skills list')
    if path == '/v1/skills':
        if handler.command == 'POST':
            created = store.put_skill(owner, upload(handler.headers.get('Content-Type', ''), handler.body(524288)))
            handler.respond(200, skill(store.skills(owner, created['id'])))
        elif handler.command == 'GET':
            if params.get('source', ['custom'])[0] != 'custom':
                raise FileProblem(400, 'Only custom Skills are available')
            grouped = {}
            for row in store.skills(owner):
                grouped.setdefault(row['id'], []).append(row)
            handler.respond(200, page([skill(rows) for rows in grouped.values()], params))
        else:
            raise FileProblem(405, 'Unsupported Skill operation')
        return
    match = re.fullmatch(r'/v1/skills/(skill_webcc_[a-f0-9]{32})(?:/versions(?:/([a-f0-9]{24}|latest)(/content)?)?)?', path)
    if not match:
        raise FileProblem(404, 'Skill route not found')
    identity, requested, content = match.groups()
    rows = store.skills(owner, identity)
    if not rows:
        raise FileProblem(404, 'Skill not found')
    selected = rows[0]['version'] if requested == 'latest' else requested
    row = next((r for r in rows if r['version'] == selected), None)
    if requested and not row:
        raise FileProblem(404, 'Skill version not found')
    if content and handler.command == 'GET':
        bundle = store.skill_version(owner, identity, selected)
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for filename, text in bundle['files'].items():
                archive.writestr(bundle['name'] + '/' + filename, text)
        handler.respond(200, data.getvalue(), 'application/zip')
    elif content:
        raise FileProblem(405, 'Unsupported content operation')
    elif handler.command == 'GET':
        result = version(row) if requested else page([version(r) for r in rows], params) if path.endswith('/versions') else skill(rows)
        if requested:
            result.update(store.skill_version(owner, identity, selected))
        elif not path.endswith('/versions'):
            result.update(data=rows, has_more=False)
        handler.respond(200, result)
    elif handler.command == 'POST' and path.endswith('/versions'):
        created = store.put_skill(owner, upload(handler.headers.get('Content-Type', ''), handler.body(524288)), identity)
        handler.respond(200, version(next(r for r in store.skills(owner, identity) if r['version'] == created['version'])))
    elif handler.command == 'DELETE':
        store.delete_skill(owner, identity, selected)
        handler.respond(200, {'id': selected or identity, 'type': 'skill_version_deleted' if selected else 'skill_deleted'})
    else:
        raise FileProblem(405, 'Unsupported Skill operation')
