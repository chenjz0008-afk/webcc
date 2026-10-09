"""Verified source locations, produced by the platform rather than native sampling."""
import base64
import copy
import json
import subprocess
import sys
import threading
from pathlib import Path
from web_files import FileProblem
from web_tools.history import bounded_json, load_json
from runtime_forward import infer
from message_stream import Stream

PDF_SLOT = threading.BoundedSemaphore(1)
SCHEMA = {'type': 'object', 'properties': {'answer': {'type': 'string', 'minLength': 1},
    'quotes': {'type': 'array', 'minItems': 1, 'maxItems': 16, 'items': {'type': 'object',
        'properties': {'document_index': {'type': 'integer', 'minimum': 0}, 'page': {'type': 'integer', 'minimum': 1},
                       'start': {'type': 'integer', 'minimum': 0}, 'quote': {'type': 'string', 'minLength': 1, 'maxLength': 2048}},
        'required': ['document_index', 'page', 'start', 'quote'], 'additionalProperties': False}}},
    'required': ['answer', 'quotes'], 'additionalProperties': False}


def pdf_pages(encoded):
    if not isinstance(encoded, str) or len(encoded) > 28 * 1024 * 1024:
        raise FileProblem(413, 'PDF exceeds size limit')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise FileProblem(400, 'Invalid PDF base64') from None
    if len(raw) > 20971520 or not raw.startswith(b'%PDF-'):
        raise FileProblem(400, 'Invalid PDF')
    if not PDF_SLOT.acquire(blocking=False):
        raise FileProblem(429, 'PDF extraction is busy')
    try:
        result = subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('pdf_text.py'))],
            input=raw, capture_output=True, timeout=8, env={}, cwd='/tmp')
        if result.returncode or len(result.stdout) > 262144:
            raise FileProblem(400, 'PDF text could not be extracted within supported limits')
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise FileProblem(400, 'PDF extraction deadline exceeded') from None
    finally:
        PDF_SLOT.release()


def prepare(fields, standard=False, cache=None, owner=None):
    if not isinstance(fields, dict) or not standard and (set(fields) - {'model', 'messages', 'max_tokens', 'stream', 'system'} or fields.get('model') != 'webcc-citations-v1'):
        raise FileProblem(400, 'Use webcc-citations-v1 with supported Messages fields')
    if type(fields.get('stream', False)) is not bool or not isinstance(fields.get('messages'), list):
        raise FileProblem(400, 'Invalid citation request')
    result, sources = copy.deepcopy(fields), []
    for message in result['messages']:
        if not isinstance(message, dict):
            raise FileProblem(400, 'Invalid message')
        blocks = message.get('content')
        if not isinstance(blocks, list):
            continue
        for index, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get('type') != 'document':
                continue
            if message.get('role') != 'user' or set(block) - {'type', 'source', 'title', 'context', 'citations'} or block.get('citations') != {'enabled': True}:
                raise FileProblem(400, 'User documents require citations.enabled=true')
            source = block.get('source', {})
            title = block.get('title', '')
            if not isinstance(title, str) or len(title) > 255 or not isinstance(source, dict):
                raise FileProblem(400, 'Invalid citation document')
            if source.get('type') == 'text' and set(source) <= {'type', 'data', 'media_type'} and source.get('media_type', 'text/plain') == 'text/plain' and isinstance(source.get('data'), str):
                kind, pages = 'char_location', [source['data']]
            elif set(source) == {'type', 'media_type', 'data'} and source.get('type') == 'base64' and source.get('media_type') == 'application/pdf':
                import hashlib
                if not isinstance(source['data'], str):
                    raise FileProblem(400, 'Invalid PDF base64')
                pages = cache.memo(owner, 'pdf-pages', hashlib.sha256(source['data'].encode()).hexdigest(), lambda: pdf_pages(source['data'])) if cache else pdf_pages(source['data'])
                kind = 'page_location'
            else:
                raise FileProblem(400, 'Citations support text, text PDF and owned file references')
            if len(sources) >= 8 or not any(p.strip() for p in pages):
                raise FileProblem(400, 'Provide one to eight nonempty citable documents')
            identity = len(sources)
            sources.append({'type': kind, 'title': title, 'pages': pages})
            blocks[index] = {'type': 'text', 'text': json.dumps({'document_index': identity, 'title': title,
                'pages': [{'page': i + 1, 'text': p} for i, p in enumerate(pages)]}, ensure_ascii=False)}
    if not sources:
        raise FileProblem(400, 'No citable documents provided')
    result.update(model=fields['model'] if standard else 'webcc-prompt-v1', stream=False, tools=[], output_config={**fields.get('output_config', {}), 'format': {'type': 'json_schema', 'schema': SCHEMA}})
    instruction = 'Answer from the supplied documents. Return answer and exact supporting quotes. For each quote provide document_index, page and zero-based Unicode start character in that page. Quote must match the original exactly. Do not use titles or context as evidence.'
    system = result.get('system', '')
    result['system'] = [*system, {'type': 'text', 'text': instruction}] if standard and isinstance(system, list) else system + '\n' + instruction
    bounded_json(result)
    return result, sources


def verify(value, sources):
    from jsonschema import Draft202012Validator
    Draft202012Validator(SCHEMA).validate(value)
    citations = []
    for quote in value['quotes']:
        doc, page, start, text = quote['document_index'], quote['page'], quote['start'], quote['quote']
        if doc >= len(sources) or page > len(sources[doc]['pages']):
            raise FileProblem(502, 'Citation points outside its document')
        source = sources[doc]
        original = source['pages'][page - 1]
        if original[start:start + len(text)] != text:
            start = original.find(text)
            if start < 0 or original.find(text, start + 1) >= 0:
                raise FileProblem(502, 'Citation quote is absent or its location is ambiguous')
        citation = {'type': source['type'], 'document_index': doc, 'document_title': source['title'], 'cited_text': text}
        if source['type'] == 'char_location':
            citation.update(start_char_index=start, end_char_index=start + len(text))
        else:
            citation.update(start_page_number=page, end_page_number=page + 1)
        citations.append(citation)
    return [{'type': 'text', 'text': value['answer'], 'citations': citations}]


def messages(handler, fields=None, standard=False):
    if not standard and (handler.path != '/v1/messages' or handler.headers.get('anthropic-beta')):
        raise FileProblem(400, 'Unsupported citation query or beta header')
    fields = load_json(handler.body(33554432)) if fields is None else fields
    fields, _ = handler.manager.files.resolve(handler.caller_key or 'platform', fields)
    request, sources = prepare(fields, standard, handler.manager.processing_cache, handler.caller_key or 'platform')
    handler.adapter = 'verified-citations-v1; platform-generated; buffered'
    watcher = Stream(handler)
    stream = watcher if fields.get('stream') else None
    try:
        if stream:
            stream.start()
        response = infer(handler.manager, handler.caller_key or 'platform', request, None if standard else 'prompt-v1', on_check=watcher.check)
        value = load_json(''.join(b['text'] for b in response['content'] if b['type'] == 'text'))
        response.update(content=[b for b in response['content'] if b['type'] in {'thinking', 'redacted_thinking'}] + verify(value, sources))
        if not standard:
            response['model'] = 'webcc-citations-v1'
        if stream:
            stream.complete(response)
        else:
            handler.respond(200, response)
    except Exception:
        if not stream or not stream.started:
            raise
        try:
            stream.error('Citation response failed validation or was interrupted')
        except OSError:
            handler.close_connection = True
