"""Server-only file lifecycle and real web-account input verification."""
import base64
import copy
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from manager import Manager, Server


def run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def main():
    production = Path('/var/lib/clewdr-manager/registry.json')
    original = production.read_bytes()
    account = next(a for a in json.loads(original)['accounts'].values() if a['name'] == 'ccb9' and a['status'] == 'ready')
    worker = 'webcc-files-proof'
    if run('docker', 'inspect', worker, check=False).returncode == 0:
        raise RuntimeError('Test worker already exists')
    report = {'date': '2026-10-07', 'candidate_only': True, 'account': 'ccb9', 'checks': []}
    with tempfile.TemporaryDirectory(prefix='webcc-files-proof-') as temporary:
        root = Path(temporary)
        server = None
        try:
            config = root / 'config'; config.mkdir(mode=0o700)
            for path in Path(account['directory']).glob('*.toml'):
                shutil.copy2(path, config / path.name)
            network = json.loads(run('docker', 'network', 'inspect', 'webcc-' + account['id']).stdout)[0]
            assert not network['EnableIPv6'] and network['Options']['com.docker.network.bridge.name'] == 'wc' + account['id']
            run('docker', 'run', '-d', '--name', worker, '--network', network['Name'], '--dns', '127.0.0.1',
                '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1', '--log-driver', 'none', '--memory', '256m',
                '--cpus', '1', '--pids-limit', '64', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '-p', '127.0.0.1:19099:8484', '-v', str(config) + ':/etc/clewdr', account['image'])
            for _ in range(30):
                c = http.client.HTTPConnection('127.0.0.1', 19099, timeout=2)
                try:
                    c.request('GET', '/api/version'); r = c.getresponse(); r.read()
                    if r.status == 200:
                        break
                except OSError:
                    pass
                finally:
                    c.close()
                time.sleep(1)
            else:
                raise RuntimeError('Test worker unavailable')
            data = root / 'data'; data.mkdir(mode=0o700)
            candidate = {**copy.deepcopy(account), 'port': 19099, 'directory': str(config)}
            (data / 'registry.json').write_text(json.dumps({'accounts': {account['id']: candidate}, 'update': {'image': account['image']}}))
            os.environ['MANAGER_WEB_TOOLS_ENABLED'] = 'true'
            manager = Manager(data, account['image'], secrets.token_urlsafe(32), secrets.token_urlsafe(32))
            server = Server(('127.0.0.1', 0), manager)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            fields = {'name': 'Files fixture A', 'accounts': [account['id']], 'scopes': ['messages', 'files'], 'rpm': 60}
            a = manager.api_keys.create(fields); b = manager.api_keys.create({**fields, 'name': 'Files fixture B'})

            def request(method, path, value=None, token=None, content_type='application/json'):
                raw = json.dumps(value).encode() if isinstance(value, dict) else value
                c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=160)
                try:
                    c.request(method, path, raw, {'x-api-key': token or a['key'], 'Content-Type': content_type})
                    r = c.getresponse(); raw = r.read(2 * 1024 * 1024)
                    return r.status, raw
                finally:
                    c.close()

            for filename, mime, raw, expected in [('allocation.pdf', 'application/pdf', (Path(__file__).parent / 'fixtures/allocation.pdf').read_bytes(), '120'),
                                                    ('allocation.txt', 'text/plain', b'North allocation is 37. South allocation is 83.', '120')]:
                boundary = 'webcc-' + secrets.token_hex(12)
                body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode() + raw + (f'\r\n--{boundary}--\r\n').encode()
                status, response = request('POST', '/v1/files?beta=true', body, content_type='multipart/form-data; boundary=' + boundary)
                assert status == 200, response
                file = json.loads(response); path = '/v1/files/' + file['id']
                assert request('GET', path + '/content') == (200, raw)
                assert request('GET', path, token=b['key'])[0] == 404
                assert request('DELETE', path, token=b['key'])[0] == 404
                assert json.loads(request('GET', '/v1/files?limit=1&beta=true')[1])['data'][0]['id'] == file['id']
                payload = {'model': 'claude-sonnet-4-6', 'stream': False, 'max_tokens': 256, 'messages': [{'role': 'user', 'content': [
                    {'type': 'document', 'source': {'type': 'file', 'file_id': file['id']}},
                    {'type': 'text', 'text': 'Read the supplied document. Add the North and South allocations. Reply only with their sum.'}]}]}
                dispatched = sum(manager.dispatches.values())
                assert request('POST', '/v1/messages', payload, token=b['key'])[0] == 404
                assert sum(manager.dispatches.values()) == dispatched
                status, response = request('POST', '/v1/messages', payload)
                text = ''.join(block.get('text', '') for block in json.loads(response).get('content', []))
                assert status == 200 and text.strip() == expected, (status, response)
                assert request('DELETE', path + '?beta=true')[0] == 200
                dispatched = sum(manager.dispatches.values())
                assert request('GET', path)[0] == 404
                assert request('POST', '/v1/messages', payload)[0] == 404
                assert sum(manager.dispatches.values()) == dispatched and manager.total == 0
                report['checks'].append({'file': filename, 'real_model_response': text, 'download_bytes_match': True,
                    'cross_key_denied': True, 'deleted_reference_denied_before_inference': True})
            report['model_requests'] = 2
            report['pass'] = True
        finally:
            if server:
                server.shutdown(); server.server_close()
            run('docker', 'rm', '-f', worker, check=False)
            report['production_registry_unchanged'] = production.read_bytes() == original
            report['temporary_container_removed'] = run('docker', 'inspect', worker, check=False).returncode != 0
    report['temporary_directory_removed'] = not root.exists()
    output = Path('/var/lib/clewdr-manager/files-candidate-result.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)); output.chmod(0o600)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
