"""Chat Completions framing over the shared Messages tool adapter."""
import json
import time
from urllib.parse import urlsplit
from uuid import uuid4
from web_tools.history import load_json
from web_files import FileProblem


def convert(fields):
    if not isinstance(fields, dict) or not isinstance(fields.get('messages'), list):
        raise FileProblem(400, 'Chat messages must be an array')
    result = {k: v for k, v in fields.items() if k not in {
        'messages', 'tools', 'tool_choice', 'parallel_tool_calls', 'max_completion_tokens',
        'response_format', 'stream_options', 'stop', 'user'}}
    result['tools'] = []
    for tool in fields.get('tools', []):
        if not isinstance(tool, dict) or tool.get('type') != 'function' or not isinstance(tool.get('function'), dict):
            raise FileProblem(400, 'Chat tools require function definitions')
        fn = tool['function']
        result['tools'].append({'name': fn.get('name'), 'description': fn.get('description', ''),
                                'input_schema': fn.get('parameters', {'type': 'object'}),
                                **({'strict': fn['strict']} if 'strict' in fn else {})})
    choice = fields.get('tool_choice', 'auto')
    if isinstance(choice, str) and choice in {'auto', 'none', 'required'}:
        choice = {'type': 'any' if choice == 'required' else choice}
    elif isinstance(choice, dict) and choice.get('type') == 'function' and isinstance(choice.get('function'), dict):
        choice = {'type': 'tool', 'name': choice['function'].get('name')}
    else:
        raise FileProblem(400, 'Invalid Chat tool_choice')
    if 'parallel_tool_calls' in fields:
        if type(fields['parallel_tool_calls']) is not bool:
            raise FileProblem(400, 'parallel_tool_calls must be boolean')
        choice['disable_parallel_tool_use'] = not fields['parallel_tool_calls']
    result['tool_choice'] = choice
    result['max_tokens'] = fields.get('max_completion_tokens', fields.get('max_tokens', 1024))
    if 'stop' in fields:
        result['stop_sequences'] = [fields['stop']] if isinstance(fields['stop'], str) else fields['stop']
    if 'user' in fields:
        result['metadata'] = {**result.get('metadata', {}), 'user_id': fields['user']}
    format_ = fields.get('response_format')
    if format_:
        schema = format_.get('json_schema', {}).get('schema') if isinstance(format_, dict) else None
        if isinstance(format_, dict) and format_.get('type') == 'json_object':
            schema = {'type': 'object'}
        if schema is None:
            raise FileProblem(400, 'Chat response_format requires json_schema or json_object')
        result['output_config'] = {**result.get('output_config', {}), 'format': {'type': 'json_schema', 'schema': schema}}
    messages, system = [], []
    for message in fields['messages']:
        if not isinstance(message, dict):
            raise FileProblem(400, 'Invalid Chat message')
        role, content = message.get('role'), message.get('content')
        if role in {'system', 'developer'}:
            if isinstance(content, str):
                system.append({'type': 'text', 'text': content})
            elif isinstance(content, list):
                system.extend(content)
            else:
                raise FileProblem(400, 'System content must be text')
            continue
        if role == 'tool':
            blocks = [{'type': 'tool_result', 'tool_use_id': message.get('tool_call_id'),
                       'content': content if content is not None else ''}]
            role = 'user'
        else:
            if role not in {'user', 'assistant'}:
                raise FileProblem(400, 'Invalid Chat message role')
            blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) and content else content or []
            if not isinstance(blocks, list):
                raise FileProblem(400, 'Invalid Chat content')
            blocks = list(blocks)
            for call in message.get('tool_calls', []):
                fn = call.get('function', {})
                blocks.append({'type': 'tool_use', 'id': call.get('id'), 'name': fn.get('name'),
                               'input': load_json(fn.get('arguments', ''))})
        if messages and messages[-1]['role'] == role:
            messages[-1]['content'].extend(blocks)
        else:
            messages.append({'role': role, 'content': blocks})
    result['messages'] = messages
    if system:
        existing = result.get('system', [])
        result['system'] = ([{'type': 'text', 'text': existing}] if isinstance(existing, str) else existing) + system
    return result


def finish_reason(reason):
    return {'tool_use': 'tool_calls', 'max_tokens': 'length', 'refusal': 'content_filter'}.get(reason, 'stop')


class ChatResponse:
    def __init__(self):
        self.id, self.created, self.model = 'chatcmpl-' + uuid4().hex, int(time.time()), 'unknown'
        self.buffer, self.tools, self.next_tool = b'', {}, 0
        self.usage, self.include_usage = {}, False

    def payload(self, delta, reason=None):
        return {'id': self.id, 'created': self.created, 'model': self.model, 'object': 'chat.completion.chunk',
                'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}

    def frame(self, value):
        kind = value.get('type')
        if kind == 'message_start':
            self.model = value['message'].get('model', 'unknown')
            self.usage = dict(value['message'].get('usage', {}))
            return [self.payload({'role': 'assistant', 'content': ''})]
        if kind == 'content_block_start' and value['content_block']['type'] == 'tool_use':
            block = value['content_block']
            self.tools[value['index']] = self.next_tool
            self.next_tool += 1
            return [self.payload({'tool_calls': [{'index': self.tools[value['index']], 'id': block['id'],
                    'type': 'function', 'function': {'name': block['name'], 'arguments': ''}}]})]
        if kind == 'content_block_delta':
            delta = value['delta']
            if delta['type'] == 'text_delta':
                return [self.payload({'content': delta['text']})]
            if delta['type'] == 'input_json_delta':
                return [self.payload({'tool_calls': [{'index': self.tools[value['index']],
                        'function': {'arguments': delta['partial_json']}}]})]
        if kind == 'message_delta':
            self.usage.update(value.get('usage', {}))
            return [self.payload({}, finish_reason(value['delta']['stop_reason']))]
        if kind == 'error':
            return [{'error': value['error']}]
        if kind == 'message_stop':
            values = []
            if self.include_usage:
                values.append({**self.payload({}), 'choices': [], 'usage': usage(self.usage)})
            return [*values, '[DONE]']
        return []

    def feed(self, data):
        self.buffer += data
        output = []
        while b'\n\n' in self.buffer:
            packet, self.buffer = self.buffer.split(b'\n\n', 1)
            payload = b'\n'.join(line[6:] for line in packet.splitlines() if line.startswith(b'data: '))
            if not payload:
                continue
            for value in self.frame(json.loads(payload)):
                output.append(('data: ' + (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)) + '\n\n').encode())
        return b''.join(output)

    def message(self, value):
        content, calls = [], []
        for block in value['content']:
            if block['type'] == 'text':
                content.append(block['text'])
            elif block['type'] == 'tool_use':
                calls.append({'id': block['id'], 'type': 'function', 'function': {
                    'name': block['name'], 'arguments': json.dumps(block['input'], ensure_ascii=False)}})
        return {'id': self.id, 'created': self.created, 'model': value['model'], 'object': 'chat.completion',
                'choices': [{'index': 0, 'finish_reason': finish_reason(value['stop_reason']), 'message': {
                    'role': 'assistant', 'content': ''.join(content) or (None if calls else ''),
                    **({'tool_calls': calls} if calls else {})}}], 'usage': usage(value.get('usage', {}))}


def usage(value):
    return {'prompt_tokens': value.get('input_tokens', 0), 'completion_tokens': value.get('output_tokens', 0),
            'total_tokens': value.get('input_tokens', 0) + value.get('output_tokens', 0)}


class Handler:
    def __init__(self, handler, fields):
        self.original, self.chat = handler, ChatResponse()
        query = urlsplit(handler.path).query
        self.path = '/v1/messages' + ('?' + query if query else '')
        self.chat.include_usage = bool((fields.get('stream_options') or {}).get('include_usage'))

    def __getattr__(self, name):
        return getattr(self.original, name)

    def send_response(self, *args):
        for name in ('request_id', 'adapter', 'retry_after'):
            if name in self.__dict__:
                setattr(self.original, name, getattr(self, name))
        self.original.send_response(*args)

    def respond(self, status, value):
        for name in ('request_id', 'adapter', 'retry_after', 'close_connection'):
            if name in self.__dict__:
                setattr(self.original, name, getattr(self, name))
        if value.get('type') == 'message':
            value = self.chat.message(value)
        elif value.get('type') == 'error':
            value = {'error': {**value['error'], 'request_id': getattr(self, 'request_id', '')}}
        self.original.respond(status, value)

    def write_stream(self, data):
        if data == b'0\r\n\r\n':
            self.original.write_stream(data)
            return
        _, payload = data.split(b'\r\n', 1)
        output = self.chat.feed(payload[:-2])
        if output:
            self.original.write_stream(('%x\r\n' % len(output)).encode() + output + b'\r\n')


def forward(handler, fields):
    from web_tools.gateway import forward as messages
    try:
        raw = json.dumps(convert(fields), ensure_ascii=False).encode()
        proxy = Handler(handler, fields)
        try:
            messages(proxy, raw=raw, standard=True)
        finally:
            for name in ('request_id', 'adapter', 'retry_after', 'close_connection'):
                if name in proxy.__dict__:
                    setattr(handler, name, getattr(proxy, name))
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        handler.close_connection = True
        handler.respond(error.status if isinstance(error, FileProblem) else 400,
                        {'error': {'type': 'invalid_request_error', 'message': str(error) if isinstance(error, FileProblem) else 'Invalid Chat tool request'}})
