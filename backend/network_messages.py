"""Brave and public-page execution over the existing account tool adapter."""
import copy
import json
import re
import threading
import time
from uuid import uuid4

from message_stream import Stream
from native_events import source_token
from network_tools import search
from source_text import text
from web_files import FileProblem

SLOTS = threading.BoundedSemaphore(4)
DEFINITIONS = {
    'web_search': {'name': 'webcc_search', 'description': 'Search the public web using Brave and return real source snippets.',
                   'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 500}},
                                    'required': ['query'], 'additionalProperties': False}},
    'web_fetch': {'name': 'webcc_fetch', 'description': 'Read actual text from a public HTTPS URL.',
                  'input_schema': {'type': 'object', 'properties': {'url': {'type': 'string', 'maxLength': 2048}},
                                   'required': ['url'], 'additionalProperties': False}},
}


def configure(fields):
    declarations, tools = {}, []
    for tool in fields.get('tools') or []:
        kind = next((name for name in DEFINITIONS if str(tool.get('type', '')).startswith(name + '_')), None)
        if kind:
            if kind in declarations:
                raise FileProblem(400, 'Duplicate network tool declaration')
            maximum = tool.get('max_uses', 5)
            if type(maximum) is not int or not 1 <= maximum <= 10:
                raise FileProblem(400, 'Network max_uses must be one to ten')
            from search_domains import SearchDomains
            SearchDomains(tool.get('allowed_domains') or [], tool.get('blocked_domains') or [], '')
            declarations[kind] = {**tool, 'max_uses': maximum}
            tools.append(copy.deepcopy(DEFINITIONS[kind]))
        else:
            tools.append(copy.deepcopy(tool))
    choice = copy.deepcopy(fields.get('tool_choice', {'type': 'auto'}))
    if isinstance(choice, dict) and choice.get('name') in declarations:
        choice['name'] = DEFINITIONS[choice['name']]['name']
    return declarations, tools, choice


def execute(manager, owner, name, arguments, declaration):
    if name == 'web_search':
        result = search(manager, owner, {**arguments, **{k: declaration[k] for k in ('allowed_domains', 'blocked_domains', 'freshness') if k in declaration}})
        sources = result['results']
        external = [{'type': 'web_search_result', 'url': s['url'], 'title': s['title'],
                     'encrypted_content': source_token(manager, owner, s)} for s in sources]
        summary = '\n'.join('- ' + s['title'] + ': ' + s['url'] + '\n' + '\n'.join(s['snippets'][:2]) for s in sources)
        return external, result, summary or 'No search result matched the requested constraints.'
    from search_domains import SearchDomains
    policy = SearchDomains(declaration.get('allowed_domains') or [], declaration.get('blocked_domains') or [], '')
    if not policy.accepts(arguments.get('url')):
        raise FileProblem(403, 'URL does not match the declared source constraints')
    page = text(manager, owner, arguments['url'])
    if not policy.accepts(page['url']):
        raise FileProblem(403, 'Redirect left the declared source constraints')
    external = {'type': 'web_fetch_result', 'url': page['url'], 'retrieved_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                'content': {'type': 'document', 'title': page['url'], 'source': {'type': 'text', 'media_type': 'text/plain', 'data': page['text']}}}
    return external, page, page['text']


def explicit_query(fields, declarations):
    if set(declarations) != {'web_search'} or len(fields.get('tools') or []) != 1:
        return None
    system = fields.get('system') or ''
    system = system if isinstance(system, str) else '\n'.join(b.get('text', '') for b in system if isinstance(b, dict))
    if 'You run a web search for another Claude model' not in system:
        return None
    content = (fields.get('messages') or [{}])[-1].get('content', '')
    content = content if isinstance(content, str) else '\n'.join(b.get('text', '') for b in content if isinstance(b, dict))
    match = re.fullmatch(r'Perform a web search for the query:\s*(.+)', content.strip(), flags=re.S)
    return match[1].strip() if match and len(match[1]) <= 500 else None


def messages(handler, fields):
    declarations, tools, choice = configure(fields)
    params = {**copy.deepcopy(fields), 'tools': tools, 'tool_choice': choice, 'stream': False}
    from web_tools.api import prepare
    prepare(json.dumps(params).encode(), getattr(handler.manager, 'files', None), standard=True,
            owner=handler.caller_key or 'platform', cache=handler.manager.processing_cache)
    if isinstance(choice, dict) and choice.get('type') == 'none':
        from web_tools.gateway import forward
        forward(handler, json.dumps(params).encode(), standard=True)
        return
    if not SLOTS.acquire(blocking=False):
        raise FileProblem(429, 'Network Messages service is busy')
    handler.request_id = uuid4().hex
    handler.adapter = 'brave-search-v1; proxy-only; real-tool-results'
    owner = handler.caller_key or 'platform'
    watcher = Stream(handler)
    stream = watcher if fields.get('stream') else None
    output, counts = [], {name: 0 for name in declarations}
    query = explicit_query(fields, declarations)
    begun = False
    result = {'id': 'msg_' + uuid4().hex, 'type': 'message', 'role': 'assistant', 'model': 'webcc-search-v1',
              'content': output, 'stop_reason': 'end_turn', 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}
    def begin(model):
        nonlocal begun
        if stream and not begun:
            stream.start()
            stream.begin({**result, 'model': model})
            begun = True
    def emit(block):
        output.append(block)
        if stream:
            stream.block(block)
    def call(kind, arguments, identity):
        watcher.check()
        call = {'type': 'server_tool_use', 'id': identity, 'name': kind, 'input': arguments}
        emit(call)
        counts[kind] += 1
        external, internal, summary = execute(handler.manager, owner, kind, arguments, declarations[kind])
        watcher.check()
        emit({'type': kind + '_tool_result', 'tool_use_id': identity, 'content': external})
        return internal, summary
    try:
        if query:
            begin('webcc-search-v1')
            _, summary = call('web_search', {'query': query}, 'srvtoolu_' + uuid4().hex)
            emit({'type': 'text', 'text': summary})
        else:
            deadline = time.monotonic() + handler.manager.request_seconds
            from runtime_forward import infer, infer_stream
            for turn in range(4):
                watcher.check()
                if time.monotonic() >= deadline:
                    raise FileProblem(504, 'Network request deadline exceeded')
                if turn == 3:
                    params['tool_choice'] = {'type': 'none'}
                def event(value):
                    if value.get('type') == 'message_start':
                        begin(value['message'].get('model', 'unknown'))
                    if value.get('type') == 'content_block_delta' and value.get('delta', {}).get('type') == 'text_delta':
                        stream.text(value['delta']['text'])
                reply = (infer_stream(handler.manager, owner, params, deadline, watcher.check, event) if stream else
                         infer(handler.manager, owner, params, deadline=deadline, on_check=watcher.check))
                result.update(model=reply.get('model', 'unknown'), usage=reply.get('usage', {}), stop_reason=reply['stop_reason'])
                pending, internal = [], []
                for block in reply['content']:
                    if block['type'] != 'tool_use':
                        output.append(block)
                        if stream and block['type'] != 'text':
                            stream.block(block)
                        continue
                    kind = next((k for k in declarations if block['name'] == DEFINITIONS[k]['name']), None)
                    if kind is None:
                        emit(block)
                        pending.append(block)
                        continue
                    if counts[kind] >= declarations[kind]['max_uses']:
                        emit({'type': 'server_tool_use', 'id': block['id'], 'name': kind, 'input': block['input']})
                        emit({'type': kind + '_tool_result', 'tool_use_id': block['id'],
                              'content': {'type': kind + '_tool_result_error', 'error_code': 'max_uses_exceeded'}})
                        result['stop_reason'] = 'pause_turn'
                        break
                    actual, _ = call(kind, block['input'], block['id'])
                    internal.append({'type': 'tool_result', 'tool_use_id': block['id'], 'content': json.dumps(actual, ensure_ascii=False)})
                if pending or result['stop_reason'] == 'pause_turn' or not internal:
                    break
                params['messages'].extend([{'role': 'assistant', 'content': reply['content']}, {'role': 'user', 'content': internal}])
                params['tool_choice'] = {'type': 'auto'}
        result['content'] = output
        if stream:
            stream.finish(result['stop_reason'], result.get('usage'))
        else:
            handler.respond(200, result)
    except Exception:
        if stream and stream.started:
            from manager import ClientGone
            try:
                stream.error('Network request interrupted; no direct fallback was used')
            except ClientGone:
                pass
        else:
            raise
    finally:
        SLOTS.release()
