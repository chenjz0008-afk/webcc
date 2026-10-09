"""Incremental envelope adaptation using ijson tokens and jiter strings."""
import json
from uuid import uuid4
import ijson
import jiter
from jsonschema import Draft202012Validator
from history_state import seal
from message_stream import event
from web_tools.discovery import visible_tools
from web_tools.history import load_json


class ToolStream:
    def __init__(self, manager, owner, request, write):
        self.manager, self.owner, self.request, self.write = manager, owner, request, write
        self.tools = {t['name']: t for t in visible_tools(request['tools'], request['messages'])}
        self.raw, self.text, self.stack, self.input_parts = b'', '', [], []
        self.index, self.current, self.active, self.calls = 0, None, None, []
        self.started, self.emitted, self.failed = False, False, False
        self.model, self.thought = 'unknown', None
        self.parser = ijson.parse_coro(self)

    def output(self, kind, **fields):
        self.emitted |= kind == 'content_block_start'
        self.write(event(kind, **fields))

    def begin(self, message):
        if not self.started:
            self.model = message.get('model') or 'unknown'
            self.output('message_start', message={**message, 'id': message.get('id') or 'msg_' + uuid4().hex,
                'type': 'message', 'role': 'assistant', 'model': self.model, 'content': [], 'stop_reason': None, 'stop_sequence': None})
            self.started = True

    def start(self, block):
        if not self.started:
            self.begin(getattr(self, 'initial', {'model': self.model, 'usage': {'input_tokens': 0, 'output_tokens': 0}}))
        self.active = block
        self.output('content_block_start', index=self.index, content_block=block)

    def stop(self):
        self.output('content_block_stop', index=self.index)
        self.active = None
        self.index += 1

    def token(self, kind, value):
        if kind in {'end_map', 'end_array'}:
            self.stack.pop()
            return '}' if kind == 'end_map' else ']'
        if kind == 'map_key':
            prefix = ',' if self.stack[-1][1] else ''
            self.stack[-1][1] += 1
            return prefix + json.dumps(value, ensure_ascii=False) + ':'
        prefix = ''
        if self.stack and self.stack[-1][0] == 'array':
            prefix = ',' if self.stack[-1][1] else ''
            self.stack[-1][1] += 1
        if kind in {'start_map', 'start_array'}:
            self.stack.append(['map' if kind == 'start_map' else 'array', 0])
            return prefix + ('{' if kind == 'start_map' else '[')
        return prefix + (str(value) if kind == 'number' else json.dumps(value, ensure_ascii=False))

    def tool_start(self):
        if self.active:
            raise ValueError('Tool call overlaps another content block')
        name = self.current['name']
        choice = self.request.get('tool_choice') or {'type': 'auto'}
        if name not in self.tools or choice['type'] == 'none' or choice['type'] == 'tool' and name != choice.get('name'):
            raise ValueError('Undeclared or disallowed tool')
        if len(self.calls) >= 8 or choice.get('disable_parallel_tool_use') and self.calls:
            raise ValueError('Tool call limit exceeded')
        self.current['id'] = 'toolu_' + uuid4().hex
        self.start({'type': 'tool_use', 'id': self.current['id'], 'name': name, 'input': {}})
        if self.input_parts:
            self.output('content_block_delta', index=self.index, delta={'type': 'input_json_delta', 'partial_json': ''.join(self.input_parts)})

    def send(self, item):
        prefix, kind, value = item
        if prefix == 'calls.item' and kind == 'start_map':
            self.current, self.input_parts = {}, []
        elif prefix == 'calls.item.name' and kind == 'string':
            self.current['name'] = value
            self.tool_start()
        elif prefix == 'calls.item.input' or prefix.startswith('calls.item.input.'):
            part = self.token(kind, value)
            self.input_parts.append(part)
            if self.active and self.active['type'] == 'tool_use':
                self.output('content_block_delta', index=self.index, delta={'type': 'input_json_delta', 'partial_json': part})
        elif prefix == 'calls.item' and kind == 'end_map':
            if 'name' not in self.current:
                raise ValueError('Missing tool name')
            inputs = load_json(''.join(self.input_parts))
            Draft202012Validator(self.tools[self.current['name']]['input_schema']).validate(inputs)
            self.calls.append({**self.current, 'input': inputs})
            self.stop()
        elif prefix == 'text' and kind == 'string' and value and self.calls:
            raise ValueError('Mixed tool calls and answer')

    def feed(self, value):
        self.raw += value.encode()
        if len(self.raw) > 131072:
            raise ValueError('Tool stream exceeds limit')
        self.parser.send(value.encode())
        partial = jiter.from_json(self.raw, partial_mode='trailing-strings')
        text = partial.get('text', '') if isinstance(partial, dict) else ''
        if isinstance(text, str) and text and not partial.get('calls'):
            if (self.request.get('tool_choice') or {}).get('type') in {'any', 'tool'}:
                raise ValueError('A tool call is required')
            if not text.startswith(self.text):
                raise ValueError('Text stream changed unexpectedly')
            if not self.active:
                self.start({'type': 'text', 'text': ''})
            if len(text) > len(self.text):
                self.output('content_block_delta', index=self.index, delta={'type': 'text_delta', 'text': text[len(self.text):]})
                self.text = text

    def upstream(self, value):
        kind = value.get('type')
        if kind == 'message_start':
            # Wait for meaningful content so malformed output can still be corrected.
            self.model = value['message'].get('model') or 'unknown'
            self.initial = value['message']
        elif kind == 'content_block_start' and value['content_block'].get('type') in {'thinking', 'redacted_thinking'}:
            self.begin(getattr(self, 'initial', {}))
            self.thought = dict(value['content_block'])
            self.start(dict(self.thought))
        elif kind == 'content_block_delta':
            delta = value['delta']
            if delta['type'] == 'text_delta':
                if not self.started:
                    self.initial = {**getattr(self, 'initial', {}), 'model': self.model}
                try:
                    self.feed(delta['text'])
                except Exception:
                    self.failed = True
                    if self.emitted:
                        raise ValueError('Incremental envelope is invalid') from None
            elif delta['type'] in {'thinking_delta', 'signature_delta'} and self.thought is not None:
                field = 'thinking' if delta['type'] == 'thinking_delta' else 'signature'
                self.thought[field] = self.thought.get(field, '') + delta[field]
                self.output('content_block_delta', index=self.index, delta=delta)
        elif kind == 'content_block_stop' and self.thought is not None:
            signed = seal(self.manager, self.owner, self.thought, self.model)
            if signed.get('signature') and not self.thought.get('signature'):
                self.output('content_block_delta', index=self.index, delta={'type': 'signature_delta', 'signature': signed['signature']})
            self.stop()
            self.thought = None

    def finish(self, message):
        if self.failed:
            raise ValueError('Invalid incremental tool response')
        self.parser.close()
        if self.active:
            self.stop()
        self.output('message_delta', delta={'stop_reason': message['stop_reason'], 'stop_sequence': message.get('stop_sequence')}, usage={'output_tokens': message['usage']['output_tokens']})
        self.output('message_stop')
