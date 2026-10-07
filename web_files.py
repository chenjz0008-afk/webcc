"""Caller-owned file storage; immutable resources expanded before web inference."""
import base64
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import os
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlencode

MAX_FILE = 20 * 1024 * 1024
OWNER_QUOTA = 256 * 1024 * 1024
TOTAL_QUOTA = 1024 * 1024 * 1024


class FileProblem(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def validate_content(mime, raw):
    signatures = {'application/pdf': b'%PDF-', 'image/png': b'\x89PNG\r\n\x1a\n',
                  'image/jpeg': b'\xff\xd8\xff', 'image/gif': b'GIF8', 'image/webp': b'RIFF'}
    if not raw or len(raw) > MAX_FILE:
        raise FileProblem(413, 'File must contain 1 byte to 20 MiB')
    if mime == 'text/plain':
        try:
            raw.decode('utf-8')
        except UnicodeError:
            raise FileProblem(400, 'Text files must use UTF-8') from None
        if b'\x00' in raw:
            raise FileProblem(400, 'Text file contains NUL bytes')
    elif mime not in signatures or not raw.startswith(signatures[mime]) or (mime == 'image/webp' and raw[8:12] != b'WEBP'):
        raise FileProblem(400, 'Unsupported file type or invalid file signature')


def parse_upload(content_type, raw):
    from python_multipart import MultipartParser
    from python_multipart.multipart import parse_options_header
    media, options = parse_options_header(content_type)
    boundary = options.get(b'boundary', b'')
    if media != b'multipart/form-data' or not 1 <= len(boundary) <= 200:
        raise FileProblem(400, 'Use multipart/form-data with a valid boundary')
    parts, current, header_name, header_value = [], {}, bytearray(), bytearray()
    finished = False

    def begin():
        if len(parts) >= 2:
            raise FileProblem(400, 'Upload accepts only file and expires_in_seconds')
        current.clear(); current.update(headers={}, data=bytearray())

    def field(data, start, end):
        header_name.extend(data[start:end])
        if len(header_name) > 128:
            raise FileProblem(400, 'Multipart header too large')

    def value(data, start, end):
        header_value.extend(data[start:end])
        if len(header_value) > 2048:
            raise FileProblem(400, 'Multipart header too large')

    def header_end():
        name = bytes(header_name).lower()
        if name in current['headers'] or len(current['headers']) >= 8:
            raise FileProblem(400, 'Invalid multipart headers')
        current['headers'][name] = bytes(header_value)
        header_name.clear(); header_value.clear()

    def data(value, start, end):
        if len(current['data']) + end - start > MAX_FILE:
            raise FileProblem(413, 'File exceeds 20 MiB')
        current['data'].extend(value[start:end])

    def part_end():
        parts.append(dict(current))

    def end():
        nonlocal finished
        finished = True

    parser = MultipartParser(boundary, {'on_part_begin': begin, 'on_header_field': field,
        'on_header_value': value, 'on_header_end': header_end, 'on_part_data': data,
        'on_part_end': part_end, 'on_end': end}, max_size=MAX_FILE + 8192)
    try:
        parser.write(raw); parser.finalize()
    except FileProblem:
        raise
    except Exception:
        raise FileProblem(400, 'Malformed multipart upload') from None
    if not finished:
        raise FileProblem(400, 'Incomplete multipart upload')
    result = {}
    for part in parts:
        disposition, params = parse_options_header(part['headers'].get(b'content-disposition', b''))
        name = params.get(b'name')
        if disposition != b'form-data' or name not in (b'file', b'expires_in_seconds') or name in result:
            raise FileProblem(400, 'Invalid or duplicate upload field')
        result[name] = (params, part)
    if b'file' not in result:
        raise FileProblem(400, 'Missing file')
    params, part = result[b'file']
    filename = params.get(b'filename', b'unnamed').decode('utf-8', errors='replace').replace('\\', '/').split('/')[-1] or 'unnamed'
    if len(filename) > 500 or any(ord(c) < 32 for c in filename):
        raise FileProblem(400, 'Invalid filename')
    mime = parse_options_header(part['headers'].get(b'content-type', b'application/octet-stream'))[0].decode('ascii')
    expiry = None
    if b'expires_in_seconds' in result:
        raw_expiry = bytes(result[b'expires_in_seconds'][1]['data'])
        if not raw_expiry.isdigit() or len(raw_expiry) > 8 or not 3600 <= int(raw_expiry) <= 7776000:
            raise FileProblem(400, 'Expiration must be 3600 to 7776000 seconds')
        expiry = int(raw_expiry)
    return filename, mime, bytes(part['data']), expiry


class FileStore:
    def __init__(self, directory):
        self.path = directory / 'files.sqlite3'
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(2)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600); os.close(fd)
        self.path.chmod(0o600)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY, owner TEXT NOT NULL, filename TEXT, mime TEXT, created REAL, expires REAL, data BLOB NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS file_owner ON files(owner, created)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA secure_delete=ON')
            with db:
                yield db
        finally:
            db.close()

    def metadata(self, row):
        return {'id': row[0], 'type': 'file', 'filename': row[2], 'mime_type': row[3],
                'size_bytes': len(row[6]), 'created_at': stamp(row[4]), 'downloadable': True,
                'expires_at': stamp(row[5]) if row[5] else None}

    def put(self, owner, filename, mime, raw, expiry=None):
        validate_content(mime, raw)
        now = time.time()
        row = ('file_webcc_' + secrets.token_hex(16), owner, filename, mime, now, now + expiry if expiry else None, raw)
        with self.lock, self.connect() as db:
            db.execute('DELETE FROM files WHERE expires IS NOT NULL AND expires <= ?', (now,))
            total = db.execute('SELECT coalesce(sum(length(data)),0) FROM files').fetchone()[0]
            owned, count = db.execute('SELECT coalesce(sum(length(data)),0),count(*) FROM files WHERE owner=?', (owner,)).fetchone()
            if total + len(raw) > TOTAL_QUOTA or owned + len(raw) > OWNER_QUOTA or count >= 128:
                raise FileProblem(413, 'File storage quota exceeded')
            db.execute('INSERT INTO files VALUES (?,?,?,?,?,?,?)', row)
        return self.metadata(row)

    def get(self, owner, identity):
        if not isinstance(identity, str):
            raise FileProblem(400, 'Invalid file ID')
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM files WHERE id=? AND owner=?', (identity, owner)).fetchone()
        if not row or row[5] and row[5] <= time.time():
            raise FileProblem(404, 'File not found')
        return row

    def delete(self, owner, identity):
        with self.lock, self.connect() as db:
            changed = db.execute('DELETE FROM files WHERE id=? AND owner=?', (identity, owner)).rowcount
            if not changed:
                raise FileProblem(404, 'File not found')
        return {'id': identity, 'type': 'file_deleted'}

    def list(self, owner, query):
        params = parse_qs(query, keep_blank_values=True, max_num_fields=105)
        if set(params) - {'limit', 'page', 'after_id', 'before_id', 'ids', 'ids[]'} or any(len(v) != 1 for k, v in params.items() if k not in ('ids', 'ids[]')):
            raise FileProblem(400, 'Invalid file list parameters')
        try:
            limit = int(params.get('limit', ['20'])[0])
        except ValueError:
            raise FileProblem(400, 'Invalid page limit') from None
        cursors = [params[k][0] for k in ('page', 'after_id', 'before_id') if k in params]
        ids = params.get('ids', []) + params.get('ids[]', [])
        if not 1 <= limit <= 1000 or len(cursors) > 1 or len(ids) > 100 or ids and (cursors or 'limit' in params):
            raise FileProblem(400, 'Invalid pagination combination')
        with self.lock, self.connect() as db:
            # Read metadata only; listing never loads all stored file bytes.
            rows = db.execute('SELECT id,owner,filename,mime,created,expires,length(data) FROM files WHERE owner=? AND (expires IS NULL OR expires>?) ORDER BY created DESC,id DESC', (owner, time.time())).fetchall()
        if cursors:
            identity = cursors[0].removeprefix('page_') if 'page' in params else cursors[0]
            index = next((i for i, row in enumerate(rows) if row[0] == identity), None)
            if index is None:
                raise FileProblem(400, 'Invalid file cursor')
            rows = rows[max(0, index - limit):index] if 'before_id' in params else rows[index + 1:]
        if ids:
            rows = [r for r in rows if r[0] in ids]
        more = not ids and len(rows) > limit
        page = rows if ids else rows[:limit]
        data = [{**self.metadata((*r[:6], b'')), 'size_bytes': r[6]} for r in page]
        return {'data': data, 'has_more': more, 'first_id': data[0]['id'] if data else None,
                'last_id': data[-1]['id'] if data else None,
                'next_page': 'page_' + data[-1]['id'] if more else None}

    def resolve(self, owner, payload):
        changed = False
        expanded = 0
        result = copy.deepcopy(payload)
        for message in result.get('messages', []) if isinstance(result, dict) else []:
            def visit(blocks):
                nonlocal changed, expanded
                if not isinstance(blocks, list):
                    return
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get('type') == 'tool_result':
                        visit(block.get('content'))
                    source = block.get('source')
                    if block.get('type') not in ('document', 'image') or not isinstance(source, dict) or source.get('type') != 'file':
                        continue
                    if message.get('role') != 'user' or set(source) != {'type', 'file_id'}:
                        raise FileProblem(400, 'Invalid file source')
                    row = self.get(owner, source['file_id']); mime, raw = row[3], row[6]
                    expanded += len(raw) * 4 // 3 + 4 if mime != 'text/plain' else len(raw)
                    if expanded > 32 * 1024 * 1024:
                        raise FileProblem(413, 'Expanded file references exceed 32 MiB')
                    if (block['type'] == 'image') != mime.startswith('image/'):
                        raise FileProblem(400, 'File type does not match content block')
                    block['source'] = {'type': 'text', 'media_type': mime, 'data': raw.decode('utf-8')} if mime == 'text/plain' else {'type': 'base64', 'media_type': mime, 'data': base64.b64encode(raw).decode()}
                    changed = True
            visit(message.get('content'))
        return result, changed


def route(handler, target):
    from manager import Problem
    handler.authenticate(scope='files')
    if not handler.manager.files.slots.acquire(blocking=False):
        raise FileProblem(429, 'File operations are busy; retry later')
    try:
        handle_route(handler, target)
    finally:
        handler.manager.files.slots.release()


def handle_route(handler, target):
    from manager import Problem
    handler.adapter = 'managed-files-v1'
    owner, store = handler.caller_key or 'platform', handler.manager.files
    params = parse_qs(target.query, keep_blank_values=True, max_num_fields=106)
    if 'beta' in params:
        if params.pop('beta') != ['true']:
            raise FileProblem(400, 'Invalid beta selector')
        target = target._replace(query=urlencode(params, doseq=True))
    path = target.path
    if path == '/v1/files':
        if handler.command == 'GET':
            handler.respond(200, store.list(owner, target.query))
        elif handler.command == 'POST' and not target.query:
            fields = parse_upload(handler.headers.get('Content-Type', ''), handler.body(MAX_FILE + 8192))
            handler.respond(200, store.put(owner, *fields))
        else:
            raise Problem(405, 'Unsupported file operation')
        return
    if target.query:
        raise Problem(400, 'File detail does not accept query parameters')
    parts = path.split('/')
    if len(parts) not in (4, 5) or len(parts) == 5 and parts[4] != 'content':
        raise Problem(404, 'File route not found')
    identity = parts[3]
    if handler.command == 'GET':
        row = store.get(owner, identity)
        handler.respond(200, row[6] if len(parts) == 5 else store.metadata(row), 'application/octet-stream' if len(parts) == 5 else 'application/json')
    elif handler.command == 'DELETE' and len(parts) == 4:
        handler.respond(200, store.delete(owner, identity))
    else:
        raise Problem(405, 'Unsupported file operation')
