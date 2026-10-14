"""Reuse gateway forwarding without storing or replaying caller credentials."""
import json
import socket
import types
import uuid
from manager import Handler, Problem
from web_files import FileProblem


class CapturedHandler(Handler):
    def __init__(self, manager, owner, params, mode):
        self.server = types.SimpleNamespace(manager=manager)
        self.caller_key = None if owner == 'platform' else owner
        self.allowed_accounts = None
        if self.caller_key:
            record = manager.api_keys.active(owner)
            needed = {'messages'} | ({'experimental_tools'} if mode else set())
            if not needed.issubset(record['scopes']):
                raise FileProblem(403, 'Task owner no longer has required message permissions')
            self.allowed_accounts = frozenset(record['accounts'])
        self.raw = json.dumps(params, ensure_ascii=False).encode()
        self.command, self.path = 'POST', '/v1/messages'
        self.headers = {'Content-Type': 'application/json', 'Content-Length': str(len(self.raw)), 'anthropic-version': '2023-06-01'}
        if mode:
            self.headers['X-WebCC-Tools'] = mode
        self.request_id = uuid.uuid4().hex
        self.retry_after = self.adapter = self.response = None
        self.connection, self.peer = socket.socketpair()

    def body(self, limit=33554432):
        if len(self.raw) > limit:
            raise FileProblem(413, 'Expanded request exceeds limit')
        return self.raw

    def respond(self, status, data, content_type=None):
        self.response = status, json.loads(data) if isinstance(data, bytes) else data


def infer(manager, owner, params, mode=None, deadline=None, on_check=None):
    handler = CapturedHandler(manager, owner, params, mode)
    handler.outer_deadline = deadline
    if on_check:
        original = handler.check_caller
        def checked():
            original()
            on_check()
        handler.check_caller = checked
    manager.background.enabled = True
    try:
        handler.forward()
        if not handler.response:
            raise FileProblem(502, 'Upstream did not produce a final message')
        status, response = handler.response
        if status != 200:
            raise FileProblem(status, response.get('error', {}).get('message', 'Message failed'))
        return response
    except Problem as error:
        raise FileProblem(error.status, str(error)) from None
    finally:
        manager.background.enabled = False
        handler.connection.close()
        handler.peer.close()


def infer_stream(manager, owner, params, deadline, on_check, emit):
    """Forward genuine tool-adapter events while collecting the validated final message."""
    from native_events import NativeEvents
    handler = CapturedHandler(manager, owner, {**params, 'stream': True}, None)
    handler.outer_deadline = deadline
    collector = NativeEvents(manager, owner)
    buffer = bytearray()
    status = [200]
    handler.send_response = lambda value: status.__setitem__(0, value)
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    original = handler.check_caller
    def active():
        original()
        on_check()
    handler.check_caller = active
    def write(raw):
        if raw == b'0\r\n\r\n':
            return
        head, data = raw.split(b'\r\n', 1)
        buffer.extend(data[:int(head, 16)])
        while b'\n\n' in buffer:
            frame, remainder = bytes(buffer).split(b'\n\n', 1)
            buffer[:] = remainder
            for line in frame.splitlines():
                if not line.startswith(b'data: '):
                    continue
                value = json.loads(line[6:])
                if value.get('type') == 'error':
                    raise FileProblem(502, value['error'].get('message', 'Model stream failed'))
                collector.collect(value)
                emit(value)
    handler.write_stream = write
    try:
        handler.forward()
        if handler.response:
            code, response = handler.response
            raise FileProblem(code if code != 200 else 502, response.get('error', {}).get('message', 'Expected model stream'))
        if status[0] != 200 or not collector.finished:
            raise FileProblem(502, 'Model stream did not complete')
        return collector.result
    except Problem as error:
        raise FileProblem(error.status, str(error)) from None
    finally:
        handler.connection.close()
        handler.peer.close()
