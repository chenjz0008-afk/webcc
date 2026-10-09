"""Explicit experimental Messages adapter; no business tools execute here."""
import json
from uuid import uuid4
from web_tools import build_prompt, parse_response, check_schema
from jsonschema import Draft202012Validator
from web_tools.discovery import visible_tools
from web_tools.media import MAX_REQUEST, separate
from web_tools.history import bounded_json, load_json

MODEL = 'webcc-prompt-v1'


def prepare(raw, files=None, owner=None, on_media=None):
    data = load_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get('messages'), list) or any(not isinstance(m, dict) for m in data['messages']):
        raise ValueError('Invalid message structure')
    for message in data['messages']:
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            blocks = block.get('content', []) if isinstance(block, dict) and block.get('type') == 'tool_result' else [block]
            if isinstance(blocks, list) and any(isinstance(b, dict) and b.get('type') in ('image', 'document') for b in blocks):
                if on_media is not None:
                    on_media()
    if files is not None:
        data, _ = files.resolve(owner, data)
    data, attachments = separate(data)
    bounded_json(data)
    if not isinstance(data, dict) or set(data) - {'model', 'max_tokens', 'messages', 'tools', 'tool_choice', 'stream', 'system', 'output_config'}:
        raise ValueError('Unsupported experimental request fields')
    if data.get('model') != MODEL:
        raise ValueError('Use the explicit webcc-prompt-v1 experimental model')
    if type(data.get('max_tokens')) is not int or not 16 <= data['max_tokens'] <= 8192:
        raise ValueError('Experimental max_tokens must be 16 to 8192')
    if type(data.get('stream', False)) is not bool:
        raise ValueError('Stream must be boolean')
    schema = output_schema(data)
    if schema is not None and 'tools' not in data:
        data['tools'] = []
    prompt = build_prompt(data.get('tools'), data.get('messages'), data.get('tool_choice'), allow_empty=schema is not None)
    if schema is not None:
        prompt += '\nFor the final answer, text must be a JSON object encoded as a string matching this schema. Keep the calls/text envelope. Schema: ' + json.dumps(schema, ensure_ascii=False)
    system = data.get('system', '')
    if not isinstance(system, str):
        raise ValueError('Experimental system supports only text')
    # Bound the full prompt, including system and protocol instructions.
    bounded_json({'system': system, 'prompt': prompt})
    upstream = {'model': 'claude-sonnet-4-6', 'max_tokens': data['max_tokens'], 'stream': False,
                'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}, *attachments] if attachments else prompt}]}
    if system:
        upstream['system'] = system
    body = json.dumps(upstream, ensure_ascii=False).encode()
    if len(body) > MAX_REQUEST:
        from web_files import FileProblem
        raise FileProblem(413, 'Expanded tool request exceeds 32 MiB')
    return data, body


class OutputProblem(ValueError):
    def __init__(self, code, message, retry=True):
        super().__init__(message)
        self.code, self.retry = code, retry


def output_schema(request):
    config = request.get('output_config')
    if config is None:
        return None
    if not isinstance(config, dict) or set(config) != {'format'}:
        raise ValueError('output_config requires format')
    format_ = config['format']
    if not isinstance(format_, dict) or set(format_) != {'type', 'schema'} or format_['type'] != 'json_schema':
        raise ValueError('Output format requires json_schema and schema')
    schema = format_['schema']
    if not isinstance(schema, dict) or schema.get('type') != 'object':
        raise ValueError('Output schema must declare object type')
    check_schema(schema)
    return schema


def complete(raw, request):
    try:
        data = load_json(raw)
        if not isinstance(data, dict) or not isinstance(data.get('content'), list):
            raise ValueError('Invalid upstream message')
        if data.get('stop_reason') == 'max_tokens':
            raise OutputProblem('output_truncated', 'Upstream output reached the token limit', False)
        if data.get('stop_reason') == 'refusal':
            raise OutputProblem('output_refused', 'Upstream refused the request', False)
        blocks = data['content']
        if not blocks or any(not isinstance(b, dict) or b.get('type') != 'text' or not isinstance(b.get('text'), str) for b in blocks):
            raise ValueError('Unsupported upstream content')
        schema = output_schema(request)
        parsed = parse_response(''.join(b['text'] for b in blocks), visible_tools(request['tools'], request['messages']), request.get('tool_choice'), allow_empty=schema is not None)
        if schema is not None and parsed['stop_reason'] == 'end_turn':
            value = load_json(parsed['content'][0]['text'])
            bounded_json(value)
            Draft202012Validator(schema).validate(value)
        usage = data.get('usage', {})
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('input_tokens', 'output_tokens')):
            raise ValueError('Upstream usage unavailable')
        return {'id': 'msg_' + uuid4().hex, 'type': 'message', 'role': 'assistant', 'model': MODEL,
                **parsed, 'stop_sequence': None,
                'usage': {k: usage[k] for k in ('input_tokens', 'output_tokens')}}
    except OutputProblem:
        raise
    except Exception:
        raise OutputProblem('output_validation_failed', 'Upstream tool or JSON output failed validation') from None


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
