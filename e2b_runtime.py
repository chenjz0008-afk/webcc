"""E2B provider. Explicit proxy, denied sandbox egress, bounded resource lifetime."""
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
from e2b import Sandbox
from e2b.sandbox.sandbox_api import SandboxQuery
from web_files import FileProblem
from skill_bundles import safe_path

ROOT = '/home/user/webcc-task'


class E2BRuntime:
    def __init__(self):
        self.key = os.environ.get('E2B_API_KEY', '')
        self.proxy = os.environ.get('MANAGER_E2B_PROXY', '')
        parsed = urlsplit(self.proxy)
        if not self.key or parsed.scheme not in {'http', 'https', 'socks5', 'socks5h'} or not parsed.hostname or not parsed.port:
            raise ValueError('E2B requires an API key and an explicit proxy; direct connections are disabled')
        if parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
            raise ValueError('Invalid E2B proxy URL')
        self.options = {'api_key': self.key, 'proxy': self.proxy, 'request_timeout': 20, 'retries': 0,
                        'api_url': 'https://api.e2b.app', 'debug': False}

    def create(self, identity, seconds):
        return Sandbox.create(template=os.environ.get('MANAGER_E2B_TEMPLATE', 'base'), timeout=seconds,
                              allow_internet_access=False, network={'allow_public_traffic': False},
                              metadata={'app': 'webcc', 'run': identity},
                              lifecycle={'on_timeout': 'kill', 'auto_resume': False}, **self.options)

    def connect(self, identity, seconds):
        sandbox = Sandbox.connect(identity, timeout=max(1, seconds), **self.options)
        sandbox.set_timeout(max(1, seconds), request_timeout=20)
        return sandbox

    def start(self, sandbox, code, tools, seconds, skills, inputs):
        sandbox.commands.run('mkdir -p ' + ROOT + '/requests ' + ROOT + '/responses ' + ROOT + '/output ' + ROOT + '/input ' + ROOT + '/skills', timeout=10)
        sandbox.files.write(ROOT + '/runner.py', Path(__file__).with_name('sandbox_runner.py').read_text())
        sandbox.files.write(ROOT + '/program.py', code)
        sandbox.files.write(ROOT + '/config.json', json.dumps({'tools': tools, 'seconds': seconds}))
        for skill in skills:
            for path, text in skill['files'].items():
                destination = ROOT + '/skills/' + skill['name'] + '/' + safe_path(path)
                sandbox.files.write(destination, text)
        for name, raw in inputs:
            sandbox.files.write(ROOT + '/input/' + safe_path(name), raw)
        command = sandbox.commands.run('ulimit -f 65536; exec python ' + ROOT + '/runner.py', background=True, timeout=seconds)
        return command.pid

    def read_json(self, sandbox, path):
        try:
            info = sandbox.files.get_info(ROOT + '/' + path)
        except Exception as error:
            from e2b.exceptions import NotFoundException
            if isinstance(error, NotFoundException):
                return None
            raise
        if info.size > 131072:
            raise FileProblem(413, 'Sandbox message exceeded 128 KiB')
        return json.loads(sandbox.files.read(ROOT + '/' + path))

    def pending(self, sandbox):
        result = []
        for entry in sandbox.files.list(ROOT + '/requests'):
            if not entry.name.endswith('.json'):
                continue
            if len(result) >= 32:
                raise FileProblem(413, 'Sandbox tool call limit reached')
            result.append(self.read_json(sandbox, 'requests/' + safe_path(entry.name)))
        return result

    def poll(self, sandbox):
        code = ("import json,pathlib; p=pathlib.Path('" + ROOT + "'); "
                "load=lambda f:json.loads(f.read_text()) if f.exists() else None; "
                "v={'done':load(p/'done.json'),'progress':load(p/'progress.json'),"
                "'calls':[load(f) for f in sorted((p/'requests').glob('*.json'))][:33]}; "
                "s=json.dumps(v); assert len(s.encode())<=262144; print(s)")
        import shlex
        result = sandbox.commands.run('python -c ' + shlex.quote(code), timeout=10)
        if result.exit_code or len(result.stdout.encode()) > 262144:
            raise FileProblem(502, 'Invalid sandbox polling result')
        return json.loads(result.stdout)

    def deliver(self, sandbox, identity, result):
        sandbox.files.write(ROOT + '/responses/' + identity + '.tmp', json.dumps(result))
        sandbox.files.rename(ROOT + '/responses/' + identity + '.tmp', ROOT + '/responses/' + identity + '.json')

    def pause(self, sandbox):
        sandbox.pause(request_timeout=20)

    def kill(self, identity):
        Sandbox.kill(identity, **self.options)

    def reconcile(self, identity):
        pages = Sandbox.list(query=SandboxQuery(metadata={'app': 'webcc', 'run': identity}), limit=20, **self.options)
        while pages.has_next:
            for sandbox in pages.next_items():
                self.kill(sandbox.sandbox_id)

    def output(self, sandbox, path):
        path = safe_path(path)
        info = sandbox.files.get_info(ROOT + '/output/' + path)
        if info.size <= 0 or info.size > 20971520:
            raise FileProblem(413, 'Generated file must be 1 byte to 20 MiB')
        raw = sandbox.files.read(ROOT + '/output/' + path, format='bytes')
        if len(raw) > 20971520:
            raise FileProblem(413, 'Generated file exceeds limit')
        return bytes(raw)
