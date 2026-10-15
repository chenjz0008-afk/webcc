"""Bounded, content-free request latency records shared by Messages adapters."""
from contextlib import contextmanager
from functools import wraps
import json
import threading
import time

CURRENT = threading.local()


class Timing:
    def __init__(self, route):
        self.route, self.began = route, time.monotonic()
        self.stages, self.firsts, self.profiles, self.accounts = {}, {}, [], []
        self.lock = threading.Lock()
        self.buffer, self.status, self.stream_error = b'', None, False

    def first(self, name):
        with self.lock:
            self.firsts.setdefault(name, round(time.monotonic() - self.began, 6))

    def observe(self, side, value):
        delta, block = value.get('delta'), value.get('content_block')
        delta = delta if isinstance(delta, dict) else {}
        block = block if isinstance(block, dict) else {}
        meaningful = any(delta.get(k) for k in ('text', 'thinking', 'partial_json')) or any(
            block.get(k) for k in ('text', 'thinking')) or block.get('type') in {'tool_use', 'server_tool_use'}
        meaningful |= any(any(c['delta'].get(k) for k in ('content', 'reasoning_content', 'tool_calls'))
                          for c in (value.get('choices') or []) if isinstance(c, dict) and isinstance(c.get('delta'), dict))
        if meaningful:
            self.first(side + '_content')
        if delta.get('text') or block.get('text') or any(
                c['delta'].get('content') for c in (value.get('choices') or [])
                if isinstance(c, dict) and isinstance(c.get('delta'), dict)):
            self.first(side + '_text')
        if delta.get('thinking') or block.get('thinking'):
            self.first(side + '_thinking')
        if side == 'client' and value.get('type') == 'error':
            self.stream_error = True

    def client(self, data):
        self.buffer += data
        lines = self.buffer.split(b'\n')
        self.buffer = lines.pop()[-131072:]
        for line in lines:
            if line.startswith(b'data:'):
                try:
                    value = json.loads(line[5:])
                    if isinstance(value, dict):
                        self.observe('client', value)
                except (ValueError, TypeError):
                    pass

    def finish(self, request_id):
        with self.lock:
            print(json.dumps({'event': 'request_timing', 'request_id': request_id, 'route': self.route,
                'status': self.status, 'stream_error': self.stream_error,
                'total_seconds': round(time.monotonic() - self.began, 6),
                'stages': {k: round(v, 6) for k, v in self.stages.items()},
                'firsts': dict(self.firsts), 'profiles': self.profiles[:5], 'accounts': self.accounts[:5]}), flush=True)


@contextmanager
def measure(name, timing=None):
    timing = timing or getattr(CURRENT, 'timing', None)
    began = time.monotonic()
    try:
        yield
    finally:
        if timing:
            with timing.lock:
                timing.stages[name] = timing.stages.get(name, 0) + time.monotonic() - began


def timed(name):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with measure(name):
                return function(*args, **kwargs)
        return call
    return decorate
