"""Launch a real server CLI inside one disposable, proxy-only test container."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path('/var/lib/webcc-cluster/cli-playground')
config = json.loads((ROOT / 'access.json').read_text())
print('\n服务器 C Code 测试台\n1. auto\n2. bypass（仅限隔离测试容器）\n3. Plan\n', flush=True)
try:
    choice = input('请选择 [1]: ').strip() or '1'
except EOFError:
    sys.exit(0)
mode = {'1': 'auto', '2': 'bypassPermissions', '3': 'plan'}.get(choice)
if mode is None:
    sys.exit('无效模式，请刷新页面重新选择。')
identity = uuid.uuid4().hex[:12]
home = ROOT / 'sessions' / mode
home.mkdir(mode=0o700, parents=True, exist_ok=True)
styles = home / 'output-styles'
styles.mkdir(mode=0o700, exist_ok=True)
shutil.copyfile(Path(__file__).with_name('english-progress.md'), styles / 'english-progress.md')
if not (home / '.claude.json').exists():
    (home / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True,
        'projects': {'/workspace': {'hasTrustDialogAccepted': True}}}))
(home / 'settings.json').write_text(json.dumps({'outputStyle': 'English progress', 'permissions': {'defaultMode': mode},
    'env': {'CLAUDE_CODE_AUTO_MODE_SERVER': '0', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'CLAUDE_CODE_MAX_RETRIES': '10', 'DISABLE_AUTOUPDATER': '1'}}))
for path in [home, *home.iterdir(), styles / 'english-progress.md']:
    os.chown(path, 1000, 1000)
    path.chmod(0o700 if path.is_dir() else 0o600)
args = ['docker', 'run', '--rm', '--init', '-it', '--name', 'webcc-browser-test-' + identity,
    '--label', 'webcc.role=cli-playground', '--network', 'webcc-tools-clients', '--hostname', 'claude-test',
    '--user', '1000:1000', '--read-only', '--memory', '256m', '--cpus', '1', '--pids-limit', '128',
    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,size=64m',
    '-v', config['cli_path'] + ':/cli:ro', '-v', str(home) + ':/config',
    '-v', str(ROOT / 'workspace') + ':/workspace', '-w', '/workspace',
    '-e', 'HOME=/config', '-e', 'CLAUDE_CONFIG_DIR=/config',
    '-e', 'ANTHROPIC_AUTH_TOKEN', '-e', 'ANTHROPIC_BASE_URL=https://165.154.205.213',
    '-e', 'HTTP_PROXY=http://webcc-tool-proxy:8888', '-e', 'HTTPS_PROXY=http://webcc-tool-proxy:8888',
    'webcc-cli-curl-test:20261014', '/cli/node_modules/.bin/claude', '--settings', '/config/settings.json',
    '--model', 'claude-sonnet-4-6']
os.environ['ANTHROPIC_AUTH_TOKEN'] = config['api_key']
os.execvp('timeout', ['timeout', '--foreground', '--signal=TERM', '1800', *args])
