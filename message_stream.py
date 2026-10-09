"""Messages SSE framing shared by validated adapters."""
import copy
import json
import select
import socket
import time


def event(kind, **fields):
    return ('event: ' + kind + '\ndata: ' + json.dumps({'type': kind, **fields}, ensure_ascii=False) + '\n\n').encode()


def events(message):
    start = copy.deepcopy(message)
    start.update(content=[], stop_reason=None, stop_sequence=None)
    if 'usage' in start:
        start['usage']['output_tokens'] = 0
    yield event('message_start', message=start)
    for index, original in enumerate(message['content']):
        block = copy.deepcopy(original)
        kind = block['type']
        if kind in {'tool_use', 'server_tool_use', 'mcp_tool_use'}:
            value = json.dumps(block['input'], ensure_ascii=False)
            block['input'] = {}
            yield event('content_block_start', index=index, content_block=block)
            for offset in range(0, len(value), 256):
                yield event('content_block_delta', index=index, delta={'type': 'input_json_delta', 'partial_json': value[offset:offset+256]})
        elif kind == 'text':
            citations = block.pop('citations', [])
            value, block['text'] = block['text'], ''
            yield event('content_block_start', index=index, content_block=block)
            for offset in range(0, len(value), 256):
                yield event('content_block_delta', index=index, delta={'type': 'text_delta', 'text': value[offset:offset+256]})
            for citation in citations:
                yield event('content_block_delta', index=index, delta={'type': 'citations_delta', 'citation': citation})
        elif kind in {'web_search_tool_result', 'web_fetch_tool_result', 'mcp_tool_result', 'code_execution_tool_result'}:
            yield event('content_block_start', index=index, content_block=block)
        else:
            raise ValueError('Unsupported adapter stream block')
        yield event('content_block_stop', index=index)
    fields = {'delta': {'stop_reason': message['stop_reason'], 'stop_sequence': message.get('stop_sequence')}}
    if 'usage' in message:
        fields['usage'] = {'output_tokens': message['usage']['output_tokens']}
    yield event('message_delta', **fields)
    yield event('message_stop')


class Stream:
    def __init__(self, handler):
        self.handler, self.started = handler, False
        self.heartbeat = time.monotonic()

    def start(self):
        self.handler.connection.settimeout(2)
        self.handler.send_response(200)
        for key, value in [('Content-Type', 'text/event-stream'), ('Transfer-Encoding', 'chunked'),
                           ('Cache-Control', 'no-store'), ('X-Accel-Buffering', 'no'),
                           ('Request-Id', self.handler.request_id), ('X-Request-Id', self.handler.request_id),
                           ('X-WebCC-Adapter', self.handler.adapter)]:
            self.handler.send_header(key, value)
        self.handler.end_headers()
        self.started = True

    def send(self, data):
        self.handler.write_stream(('%x\r\n' % len(data)).encode() + data + b'\r\n')

    def check(self):
        from manager import ClientGone
        self.handler.check_caller()
        if select.select([self.handler.connection], [], [], 0)[0] and not self.handler.connection.recv(1, socket.MSG_PEEK):
            raise ClientGone()
        if self.started and time.monotonic() - self.heartbeat >= 5:
            self.send(event('ping'))
            self.heartbeat = time.monotonic()

    def complete(self, message):
        for data in events(message):
            self.check()
            self.send(data)
        self.handler.write_stream(b'0\r\n\r\n')

    def error(self, message, kind='api_error'):
        self.send(event('error', error={'type': kind, 'message': message}))
        self.handler.write_stream(b'0\r\n\r\n')
