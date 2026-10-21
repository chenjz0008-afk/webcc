"""Install a temporary, authenticated ttyd page backed by an isolated server CLI."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

import httpx
from dotenv import dotenv_values

ROOT = Path('/var/lib/webcc-cluster/cli-playground')
TARGET = Path('/opt/webcc-cli-test')
NGINX = Path('/etc/nginx/sites-enabled/clewdr').resolve()
os.umask(0o077)
env = dotenv_values('/etc/clewdr-manager.env')
os.environ.update({k: v for k, v in env.items() if v is not None})
sys.path.insert(0, '/opt/clewdr-manager')
from network_tools import proxy


def run(*args, **options):
    return subprocess.run(args, check=True, capture_output=True, **options)


def admin(path, data=None):
    request = urllib.request.Request('http://127.0.0.1:9000' + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Authorization': 'Bearer ' + env['CLEWDR_ADMIN_PASSWORD'], 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


assert os.geteuid() == 0, 'Run on the server as root'
assert not (ROOT / 'access.json').exists(), 'Test page is already installed'
cli = Path('/var/lib/webcc-cluster/cli-diagnostic-20261010/cli')
assert (cli / 'node_modules/.bin/claude').exists(), 'Verified server CLI installation is missing'
assert NGINX.is_file(), 'Expected server Nginx site is missing'
ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
TARGET.mkdir(mode=0o700, parents=True, exist_ok=True)
with httpx.Client(proxy=proxy(), trust_env=False, timeout=45, follow_redirects=True) as client:
    response = client.get('https://api.github.com/repos/tsl0922/ttyd/releases/latest')
    response.raise_for_status()
    release = response.json()
    asset = next(a for a in release['assets'] if a['name'] == 'ttyd.x86_64')
    response = client.get(asset['browser_download_url'])
    response.raise_for_status()
    binary = response.content
    checksum = hashlib.sha256(binary).hexdigest()
    if asset.get('digest'):
        assert asset['digest'] == 'sha256:' + checksum, 'ttyd release digest mismatch'
(TARGET / 'ttyd').write_bytes(binary)
(TARGET / 'ttyd').chmod(0o700)
run(str(TARGET / 'ttyd'), '--version')
source = Path(__file__).with_name('cli-test')
shutil.copy2(source / 'launch.py', TARGET / 'launch.py')
shutil.copy2(source / 'english-progress.md', TARGET / 'english-progress.md')
shutil.copy2(source / 'webcc-cli-test.service', '/etc/systemd/system/webcc-cli-test.service')
shutil.copy2(source / 'nginx.conf', '/etc/nginx/snippets/webcc-cli-test.conf')
workspace = ROOT / 'workspace'
workspace.mkdir(mode=0o700, exist_ok=True)
(workspace / 'README.md').write_text('# 隔离测试项目\n\n只允许修改本目录。建议：实现 CSV 订单汇总，金额使用 Decimal，补充并运行 unittest 测试。\n')
for path in [workspace, *workspace.iterdir()]:
    os.chown(path, 1000, 1000)
    path.chmod(0o700 if path.is_dir() else 0o600)
password = secrets.token_urlsafe(18)
username = 'webcc-test'
hash_value = run('openssl', 'passwd', '-apr1', '-stdin', input=password.encode()).stdout.decode().strip()
htpasswd = Path('/etc/nginx/webcc-cli-test.htpasswd')
htpasswd.write_text(username + ':' + hash_value + '\n')
shutil.chown(htpasswd, user='root', group='www-data')
htpasswd.chmod(0o640)
original = NGINX.read_text()
(ROOT / 'nginx.before').write_text(original)
assert 'include /etc/nginx/snippets/webcc-cli-test.conf;' not in original
position = original.index('location /', original.index('ssl_certificate'))
updated = original[:position] + 'include /etc/nginx/snippets/webcc-cli-test.conf;\n    ' + original[position:]
key = None
try:
    accounts = admin('/admin/accounts')['accounts']
    ready = [a['id'] for a in accounts if a['status'] == 'ready' and
        max(a.get('cooldown_until', 0), a.get('tools_unavailable_until', 0)) <= time.time()]
    assert ready, 'No eligible accounts'
    expires = int(time.time()) + 86400
    key = admin('/admin/api-keys', {'name': 'Server CLI playground', 'accounts': ready,
        'scopes': ['messages', 'models'], 'rpm': 60, 'expires_at': expires})
    (ROOT / 'access.json').write_text(json.dumps({'api_key': key['key'], 'api_key_id': key['id'],
        'cli_path': str(cli), 'expires_at': expires, 'ttyd_release': release['tag_name'], 'ttyd_sha256': checksum}))
    NGINX.write_text(updated)
    run('nginx', '-t')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'start', 'webcc-cli-test')
    run('systemctl', 'reload', 'nginx')
    access = {'url': 'https://165.154.205.213/claude-test/', 'username': username,
        'password': password, 'expires_at': expires, 'cli_version': '2.1.296'}
    (ROOT / 'browser-access.json').write_text(json.dumps(access, ensure_ascii=False, indent=2))
    temporary = Path('/tmp/webcc-cli-browser-access.json')
    temporary.write_text(json.dumps(access, ensure_ascii=False, indent=2))
    os.chown(temporary, 1000, 1000)
    temporary.chmod(0o600)
    print(json.dumps({k: v for k, v in access.items() if k != 'password'}))
except Exception:
    if key:
        admin('/admin/api-keys/' + key['id'] + '/revoke', {})
    NGINX.write_text(original)
    subprocess.run(['systemctl', 'stop', 'webcc-cli-test'], capture_output=True)
    subprocess.run(['systemctl', 'reload', 'nginx'], capture_output=True)
    (ROOT / 'access.json').unlink(missing_ok=True)
    raise
