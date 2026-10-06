"""Explicit experimental Messages adapter; no business tools execute here."""
import json
from uuid import uuid4
from web_tools import build_prompt, parse_response
from web_tools.history import bounded_json

MODEL = 'webcc-prompt-v1'


def prepare(raw):
    def unique(pairs):
        obj = {}
        for name, value in pairs:
            if name in obj:
                raise ValueError('Duplicate request key')
            obj[name] = value
        return obj
    data = json.loads(raw, object_pairs_hook=unique)
    bounded_json(data)
    if not isinstance(data, dict) or set(data) - {'model', 'max_tokens', 'messages', 'tools', 'tool_choice', 'stream', 'system'}:
        raise ValueError('Unsupported experimental request fields')
    if data.get('model') != MODEL:
        raise ValueError('Use the explicit webcc-prompt-v1 experimental model')
    if type(data.get('max_tokens')) is not int or not 16 <= data['max_tokens'] <= 8192:
        raise ValueError('Experimental max_tokens must be 16 to 8192')
    if type(data.get('stream', False)) is not bool:
        raise ValueError('Stream must be boolean')
    prompt = build_prompt(data.get('tools'), data.get('messages'), data.get('tool_choice'))
    system = data.get('system', '')
    if not isinstance(system, str):
        raise ValueError('Experimental system supports only text')
    # Bound the full prompt, including system and protocol instructions.
    bounded_json({'system': system, 'prompt': prompt})
    upstream = {'model': 'claude-sonnet-4-6', 'max_tokens': data['max_tokens'], 'stream': False,
                'messages': [{'role': 'user', 'content': prompt}]}
    if system:
        upstream['system'] = system
    return data, json.dumps(upstream, ensure_ascii=False).encode()


def complete(raw, request):
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get('content'), list):
        raise ValueError('Invalid upstream message')
    if data.get('stop_reason') in ('max_tokens', 'refusal'):
        raise ValueError('Upstream response truncated or refused')
    blocks = data['content']
    if not blocks or any(not isinstance(b, dict) or b.get('type') != 'text' or not isinstance(b.get('text'), str) for b in blocks):
        raise ValueError('Unsupported upstream content')
    parsed = parse_response(''.join(b['text'] for b in blocks), request['tools'], request.get('tool_choice'))
    usage = data.get('usage', {})
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('input_tokens', 'output_tokens')):
        raise ValueError('Upstream usage unavailable')
    return {'id': 'msg_' + uuid4().hex, 'type': 'message', 'role': 'assistant', 'model': MODEL,
            **parsed, 'stop_sequence': None,
            'usage': {k: usage[k] for k in ('input_tokens', 'output_tokens')}}


def events(message):
    def emit(kind, **data):
        return ('event: ' + kind + '\ndata: ' + json.dumps({'type': kind, **data}, ensure_ascii=False) + '\n\n').encode()
    start = {**message, 'content': [], 'stop_reason': None, 'usage': {**message['usage'], 'output_tokens': 0}}
    yield emit('message_start', message=start)
    for index, block in enumerate(message['content']):
        tool = block['type'] == 'tool_use'
        initial = {**block, 'input': {}} if tool else {'type': 'text', 'text': ''}
        yield emit('content_block_start', index=index, content_block=initial)
        value = json.dumps(block['input'], ensure_ascii=False) if tool else block['text']
        for offset in range(0, len(value), 256):
            delta = {'type': 'input_json_delta' if tool else 'text_delta',
                     'partial_json' if tool else 'text': value[offset:offset + 256]}
            yield emit('content_block_delta', index=index, delta=delta)
        yield emit('content_block_stop', index=index)
    yield emit('message_delta', delta={'stop_reason': message['stop_reason'], 'stop_sequence': None},
               usage={'output_tokens': message['usage']['output_tokens']})
    yield emit('message_stop')
