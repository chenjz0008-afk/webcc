"""Runs inside E2B only. File messages bridge awaited client tools."""
import ast
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import time
import traceback
import uuid

ROOT = Path('/home/user/webcc-task')


def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(path)


class BoundedOutput(io.StringIO):
    def __init__(self, notify=None):
        super().__init__()
        self.notify = notify

    def write(self, value):
        remaining = max(0, 16384 - self.tell())
        super().write(value[:remaining])
        if self.notify:
            self.notify()
        return len(value)

    def flush(self):
        if self.notify:
            self.notify(True)


async def main():
    config = json.loads((ROOT / 'config.json').read_text())
    deadline = time.monotonic() + config['seconds']
    last_progress = 0
    def progress(force=False):
        nonlocal last_progress
        now = time.monotonic()
        if force or now - last_progress >= .05:
            atomic(ROOT / 'progress.json', {'stdout': stdout.getvalue(), 'stderr': stderr.getvalue()})
            last_progress = now
    stdout, stderr = BoundedOutput(progress), BoundedOutput(progress)
    slots = asyncio.Semaphore(8)
    count = 0

    def wrapper(name):
        async def invoke(arguments):
            nonlocal count
            async with slots:
                count += 1
                if count > 32:
                    raise RuntimeError('Client tool call limit reached')
                identity = 'toolu_' + uuid.uuid4().hex
                path = ROOT / 'requests' / (identity + '.json')
                atomic(path, {'id': identity, 'name': name, 'input': arguments})
                response = ROOT / 'responses' / (identity + '.json')
                while not response.exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Client tool result deadline exceeded')
                    await asyncio.sleep(.1)
                result = json.loads(response.read_text())
                if result.get('is_error'):
                    raise RuntimeError('Client tool failed: ' + result['content'][:2048])
                return result['content']
        return invoke

    scope = {t['name']: wrapper(t['name']) for t in config['tools']}
    scope['__name__'] = '__main__'
    result = {'stdout': '', 'stderr': '', 'error': None}
    try:
        os.chdir(ROOT)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = compile((ROOT / 'program.py').read_text(), 'program.py', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
            coroutine = eval(code, scope)
            if coroutine is not None:
                await asyncio.wait_for(coroutine, max(.1, deadline - time.monotonic()))
    except BaseException as error:
        result['error'] = {'type': type(error).__name__, 'message': str(error)[:2048]}
    result.update(stdout=stdout.getvalue(), stderr=stderr.getvalue())
    atomic(ROOT / 'done.json', result)


if __name__ == '__main__':
    asyncio.run(main())
