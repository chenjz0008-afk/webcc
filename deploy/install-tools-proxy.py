"""Install Ubuntu's Tinyproxy in a private, proxy-only Docker network."""
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values
import httpx


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if result.returncode: raise RuntimeError('Tool proxy setup failed: ' + args[0])
    return result.stdout.strip()


def install():
    if os.getuid() != 0: raise SystemExit('Run with sudo and the WebCC Python environment')
    os.environ.update({k: v for k, v in dotenv_values('/etc/clewdr-manager.env').items() if v is not None})
    sys.path.insert(0, '/opt/clewdr-manager')
    from network_tools import proxy
    from egress import endpoint, prepare
    value = proxy(); parsed = urlsplit(value); address, port = endpoint(value)
    root = Path('/var/lib/webcc-cluster/tools-proxy'); root.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = run('apt-cache', 'show', 'tinyproxy-bin').split('\n\n')[0]
    package = dict(line.split(': ', 1) for line in metadata.splitlines() if ': ' in line and not line.startswith(' '))
    archive = root / 'tinyproxy.deb'
    with httpx.Client(proxy=value, trust_env=False, timeout=30, follow_redirects=False) as client:
        response = client.get('https://archive.ubuntu.com/ubuntu/' + package['Filename'])
        response.raise_for_status()
        if len(response.content) > 1048576 or hashlib.sha256(response.content).hexdigest() != package['SHA256']:
            raise RuntimeError('Tinyproxy package integrity check failed')
        archive.write_bytes(response.content)
    run('dpkg-deb', '-x', str(archive), str(root / 'package'))
    kind = 'socks5' if parsed.scheme.startswith('socks5') else 'http'
    if parsed.scheme == 'https': raise RuntimeError('Tinyproxy upstream requires HTTP or SOCKS5; no downgrade is permitted')
    credentials = ''
    if parsed.username:
        username, password = unquote(parsed.username), unquote(parsed.password or '')
        if re.search(r'[\s:@]', username + password): raise RuntimeError('Proxy credentials contain unsupported Tinyproxy config delimiters')
        credentials = username + ':' + password + '@'
    domains = os.environ.get('MANAGER_CURL_DOMAINS', 'docs.python.org,github.com,api.github.com,raw.githubusercontent.com,codeload.github.com,trade.ec.europa.eu,httpbin.org,api.search.brave.com,claude.ai,api.anthropic.com,165.154.205.213').split(',')
    if any(not re.fullmatch(r'[a-zA-Z0-9.-]+', host) for host in domains): raise RuntimeError('Invalid curl domain whitelist')
    filters = root / 'allowed-domains'; filters.write_text('\n'.join('^' + re.escape(host) + '$' for host in domains) + '\n')
    config = root / 'tinyproxy.conf'
    config.write_text('Port 8888\nListen 0.0.0.0\nTimeout 30\nMaxClients 8\nLogLevel Error\n'
                      'DisableViaHeader Yes\nXTinyproxy No\nConnectPort 443\n'
                      'Filter "/etc/webcc-domains"\nFilterDefaultDeny Yes\nFilterURLs No\n'
                      'Upstream ' + kind + ' ' + credentials + address + ':' + str(port) + '\n')
    for path in [config, filters]: path.chmod(0o600); os.chown(path, 65534, 65534)
    if subprocess.run(['docker', 'image', 'inspect', 'ubuntu:24.04'], capture_output=True).returncode:
        run('docker', 'pull', 'ubuntu:24.04')
    image = run('docker', 'image', 'inspect', 'ubuntu:24.04', '--format', '{{.Id}}')
    args = prepare('/var/lib/clewdr-manager', 'f0b1a2c3d4e5', value)
    clients = 'webcc-tools-clients'
    if subprocess.run(['docker', 'network', 'inspect', clients], capture_output=True).returncode:
        run('docker', 'network', 'create', '--internal', clients)
    subprocess.run(['docker', 'rm', '-f', 'webcc-tool-proxy'], capture_output=True)
    run('docker', 'run', '-d', '--name', 'webcc-tool-proxy', '--restart', 'unless-stopped', *args,
        '--user', '65534:65534', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--memory', '48m', '--cpus', '.25', '--pids-limit', '32', '--tmpfs', '/tmp:rw,size=8m',
        '-v', str(root / 'package/usr/bin/tinyproxy') + ':/usr/local/bin/tinyproxy:ro',
        '-v', str(config) + ':/etc/webcc-proxy.conf:ro', '-v', str(filters) + ':/etc/webcc-domains:ro',
        image, '/usr/local/bin/tinyproxy', '-d', '-c', '/etc/webcc-proxy.conf')
    run('docker', 'network', 'connect', clients, 'webcc-tool-proxy')
    print('Tool proxy installed on private Docker network; no public port or upstream credential exposed')


if __name__ == '__main__': install()
