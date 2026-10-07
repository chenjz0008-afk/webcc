"""Run isolated SDK and UI verification on the deployment server as root."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import ssl
import sys
import subprocess
import tarfile
import tempfile
import time
import urllib.request


def run(args, cwd=None, env=None, timeout=180, check=True):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} failed: {result.stderr[-1500:]}')
    return result


def main():
    registry = Path('/var/lib/clewdr-manager/registry.json')
    initial = registry.read_bytes()
    document_review = '--document-review' in sys.argv
    basic_capabilities = '--basic-capabilities' in sys.argv
    skip_ui = document_review or basic_capabilities
    name = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--account=')), 'cc1')
    account = next(a for a in json.loads(initial)['accounts'].values() if a['name'] == name and a['status'] == 'ready')
    worker = 'webcc-ts-proof'
    if run(['docker', 'inspect', worker], check=False).returncode == 0:
        raise RuntimeError('Test container already exists')
    network = 'webcc-' + account['id']
    info = json.loads(run(['docker', 'network', 'inspect', network]).stdout)[0]
    if info['EnableIPv6'] or info['Options'].get('com.docker.network.bridge.name') != 'wc' + account['id']:
        raise RuntimeError('Protected account network is required')
    report = {'date': '2026-10-07', 'account': account['name'], 'candidate_only': True}
    with tempfile.TemporaryDirectory(prefix='webcc-sdk-proof-') as temporary:
        root = Path(temporary)
        try:
            version = 'v24.21.0'
            filename = f'node-{version}-linux-x64.tar.xz'
            url = f'https://nodejs.org/dist/{version}/'
            archive = root / filename
            urllib.request.urlretrieve(url + filename, archive)
            sums = urllib.request.urlopen(url + 'SHASUMS256.txt').read().decode()
            expected = next(line.split()[0] for line in sums.splitlines() if line.endswith('  ' + filename))
            if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
                raise RuntimeError('Node checksum mismatch')
            with tarfile.open(archive) as tar:
                tar.extractall(root, filter='data')
            runtime = root / f'node-{version}-linux-x64'
            node = runtime / 'bin/node'
            npm = runtime / 'lib/node_modules/npm/bin/npm-cli.js'
            env = {'PATH': str(runtime / 'bin') + ':/usr/bin:/bin', 'HOME': str(root),
                   'npm_config_cache': str(root / 'cache'), 'npm_config_userconfig': '/dev/null',
                   'NODE_OPTIONS': '--max-old-space-size=384'}
            sdk = root / 'sdk'; sdk.mkdir()
            run([str(node), str(npm), 'install', '--prefix', str(sdk), '--ignore-scripts', '--no-audit', '--no-fund', '@anthropic-ai/sdk@0.131.0'] + (['markdown-it@14.1.0'] if document_review else []), env=env)
            source = Path(__file__).parent
            report['frontend_build'] = {'pass': True, 'skipped': skip_ui}
            if not skip_ui:
                ui = root / 'frontend'; shutil.copytree(source.parent / 'frontend', ui)
                run([str(node), str(npm), 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=ui, env=env)
                build = run([str(node), str(npm), 'run', 'build'], cwd=ui, env=env)
                report['frontend_build'] = {'pass': (root / 'static/index.html').exists(), 'summary': build.stdout[-2000:]}
            if '--frontend-only' in sys.argv:
                output = Path('/var/lib/clewdr-manager/frontend-build-verification.json')
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2)); os.chmod(output, 0o600)
                print(json.dumps(report, ensure_ascii=False), flush=True)
                return
            script = sdk / 'sdk_request.mjs'; shutil.copy2(source / 'sdk_request.mjs', script)
            if basic_capabilities:
                shutil.copytree(source / 'fixtures', sdk / 'fixtures')
            if document_review:
                shutil.copy2(source / 'render_markdown.mjs', sdk / 'render_markdown.mjs')
            config = root / 'config'; config.mkdir(mode=0o700)
            for path in Path(account['directory']).glob('*.toml'):
                shutil.copy2(path, config / path.name)
            run(['docker', 'run', '-d', '--name', worker, '--network', network, '--dns', '127.0.0.1',
                 '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1', '--log-driver', 'none', '--memory', '256m',
                 '--cpus', '1', '--pids-limit', '64', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                 '-p', '127.0.0.1:19096:8484', '-v', str(config) + ':/etc/clewdr', account['image']])
            for attempt in range(20):
                connection = http.client.HTTPConnection('127.0.0.1', 19096, timeout=2)
                try:
                    connection.request('GET', '/api/version')
                    response = connection.getresponse(); response.read()
                    if response.status == 200:
                        break
                except OSError:
                    pass
                finally:
                    connection.close()
                time.sleep(1)
            else:
                raise RuntimeError('Test worker did not become ready')
            cert, private = root / 'cert.pem', root / 'key.pem'
            run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-keyout', str(private),
                 '-out', str(cert), '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost'])
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, private)
            from experiments.typescript_cases import run_typescript_case
            report['sdk'] = run_typescript_case(account, node, script, True, context, cert, not skip_ui, True, document_review, basic_capabilities)
            report['pass'] = report['sdk']['pass'] and report['frontend_build']['pass']
        finally:
            run(['docker', 'rm', '-f', worker], check=False)
            report['production_registry_unchanged'] = registry.read_bytes() == initial
            report['temporary_container_removed'] = run(['docker', 'inspect', worker], check=False).returncode != 0
    report['temporary_directory_removed'] = not root.exists()
    output = Path('/var/lib/clewdr-manager') / (f'basic-capabilities-{name}.json' if basic_capabilities else f'document-review-{name}.json' if document_review else 'scoped-sdk-verification.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)); os.chmod(output, 0o600)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
