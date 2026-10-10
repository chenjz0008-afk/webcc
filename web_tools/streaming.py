"""Incremental envelope adaptation using ijson tokens and jiter strings."""
import json
from uuid import uuid4
import ijson
import jiter
from history_state import seal
from message_stream import event, block_events
from web_tools.discovery import visible_tools
from web_tools.history import load_json, MAX_BYTES
from web_tools import validate_call


class ToolStream:
    def __init__(self, manager, owner, request, write):
        self.manager, self.owner, self.request, self.write = manager, owner, request, write
        self.tools = {t['name']: t for t in visible_tools(request['tools'], request['messages'])}
        self.raw, self.text, self.stack, self.input_parts = b'', '', [], []
        self.root_text, self.root_emitted = '', 0
        self.strict = any(t.get('strict') and not t.get('eager_input_streaming') for t in self.tools.values())
        self.index, self.current, self.active, self.calls = 0, None, None, []
        self.started, self.emitted, self.failed = False, False, False
        self.model, self.thought = 'unknown', None
        self.pending_thoughts = []
        self.root_keys, self.call_keys = set(), set()
        self.calls_closed = False
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
            for thought in self.pending_thoughts:
                for data in block_events([thought], self.index):
                    self.write(data)
                self.emitted = True
                self.index += 1
            self.pending_thoughts.clear()

    def start(self, block):
        if not self.started:
            self.begin(getattr(self, 'initial', {'model': self.model, 'usage': {'input_tokens': 0, 'output_tokens': 0}}))
        self.active = block
        self.output('content_block_start', index=self.index, content_block=block)

    def stop(self):
        self.output('content_block_stop', index=self.index)
        self.active = None
        self.index += 1

    def append_text(self, value):
        if value:
            if not self.active:
                self.start({'type': 'text', 'text': ''})
            self.output('content_block_delta', index=self.index, delta={'type': 'text_delta', 'text': value})
            self.text += value

    def flush_text(self):
        self.append_text(self.root_text[self.root_emitted:])
        self.root_emitted = len(self.root_text)

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
            if self.active['type'] != 'text':
                raise ValueError('Tool call overlaps another content block')
            self.stop()
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
        if prefix == 'calls' and kind == 'end_array':
            self.calls_closed = True
        elif prefix == '' and kind == 'map_key':
            if value not in {'calls', 'text'} or value in self.root_keys:
                raise ValueError('Unknown or duplicate envelope key')
            self.root_keys.add(value)
        elif prefix == 'calls.item' and kind == 'map_key':
            if value not in {'name', 'input', 'text', 'type', 'id'} or value in self.call_keys and value != 'name':
                raise ValueError('Unknown or duplicate call key')
            self.call_keys.add(value)
        elif prefix == 'text' and kind == 'string':
            self.root_text = value
        elif prefix == 'calls.item.text':
            if kind != 'string':
                raise ValueError('Call explanation must be text')
            self.current['text'] = value
        elif prefix == 'calls.item.type':
            if kind != 'string' or value not in {'tool_use', 'text'}:
                raise ValueError('Unknown content metadata type')
            self.current['type'] = value
        elif prefix == 'calls.item.id':
            import re
            if kind != 'string' or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value):
                raise ValueError('Invalid model tool metadata ID')
        elif prefix == 'calls.item' and kind == 'start_map':
            self.current, self.input_parts = {}, []
            self.call_keys = set()
        elif prefix == 'calls.item.name':
            if kind != 'string':
                raise ValueError('Tool name must be a string')
            if 'name' in self.current:
                if self.current['name'] != value:
                    raise ValueError('Conflicting duplicate tool name')
            else:
                self.current['name'] = value
                tool = self.tools.get(value, {})
                self.current['buffered'] = tool.get('eager_input_streaming', not tool.get('strict', False)) is False
                if not self.current['buffered']:
                    self.tool_start()
        elif prefix == 'calls.item.input' or prefix.startswith('calls.item.input.'):
            part = self.token(kind, value)
            self.input_parts.append(part)
            if self.active and self.active['type'] == 'tool_use':
                self.output('content_block_delta', index=self.index, delta={'type': 'input_json_delta', 'partial_json': part})
        elif prefix == 'calls.item' and kind == 'end_map':
            if self.current.get('type') == 'text':
                if self.call_keys != {'type', 'text'}:
                    raise ValueError('Invalid text content block')
                self.flush_text()
                self.append_text(self.current['text'])
                return
            if 'name' not in self.current:
                raise ValueError('Missing tool name')
            if 'id' in self.call_keys and 'type' not in self.call_keys:
                raise ValueError('Tool metadata ID requires tool_use type')
            inputs = load_json(''.join(self.input_parts))
            validate_call({'name': self.current['name'], 'input': inputs}, list(self.tools.values()),
                          self.request.get('tool_choice'), len(self.calls))
            if self.current.get('buffered'):
                self.flush_text()
                self.tool_start()
            self.calls.append({**self.current, 'input': inputs})
            self.stop()
            self.append_text(self.current.get('text', ''))

    def feed(self, value):
        self.raw += value.encode()
        if len(self.raw) > MAX_BYTES:
            raise ValueError('Tool stream exceeds limit')
        self.parser.send(value.encode())
        if 'text' not in self.root_keys or self.active and self.active['type'] != 'text':
            return
        partial = jiter.from_json(self.raw, partial_mode='trailing-strings')
        text = partial.get('text', '') if isinstance(partial, dict) else ''
        if isinstance(text, str) and text and (not self.active or self.active['type'] == 'text'):
            if self.calls_closed and not self.calls and (self.request.get('tool_choice') or {}).get('type') in {'any', 'tool'}:
                raise ValueError('A tool call is required')
            if not text.startswith(self.root_text):
                raise ValueError('Text stream changed unexpectedly')
            self.root_text = text
            if not self.strict or self.calls or self.calls_closed:
                self.flush_text()

    def upstream(self, value):
        kind = value.get('type')
        if kind == 'message_start':
            # Wait for meaningful content so malformed output can still be corrected.
            self.model = value['message'].get('model') or 'unknown'
            self.initial = value['message']
        elif kind == 'content_block_start' and value['content_block'].get('type') in {'thinking', 'redacted_thinking'}:
            self.thought = dict(value['content_block'])
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
        elif kind == 'content_block_stop' and self.thought is not None:
            self.pending_thoughts.append(seal(self.manager, self.owner, self.thought, self.model))
            self.thought = None

    def finish(self, message):
        if self.failed:
            raise ValueError('Invalid incremental tool response')
        self.parser.close()
        tools = [b for b in message['content'] if b['type'] == 'tool_use']
        expected = [{'name': b['name'], 'input': b['input']} for b in tools]
        actual = [{'name': b['name'], 'input': b['input']} for b in self.calls]
        text = ''.join(b['text'] for b in message['content'] if b['type'] == 'text')
        if expected != actual or text != self.text:
            raise ValueError('Incremental and final content differ')
        if self.active:
            self.stop()
        self.output('message_delta', delta={'stop_reason': message['stop_reason'], 'stop_sequence': message.get('stop_sequence')}, usage={'output_tokens': message['usage']['output_tokens']})
        self.output('message_stop')
