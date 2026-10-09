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


def infer(manager, owner, params, mode=None):
    handler = CapturedHandler(manager, owner, params, mode)
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
