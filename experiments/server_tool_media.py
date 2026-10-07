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
    worker = 'webcc-media-proof'
    if run('docker', 'inspect', worker, check=False).returncode == 0:
        raise RuntimeError('Test worker already exists')
    report = {'date': '2026-10-07', 'candidate_only': True, 'account': 'ccb9', 'checks': []}
    with tempfile.TemporaryDirectory(prefix='webcc-media-proof-') as temporary:
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
            fields = {'name': 'Media tools fixture A', 'accounts': [account['id']], 'scopes': ['messages', 'files', 'experimental_tools'], 'rpm': 60}
            a = manager.api_keys.create(fields); b = manager.api_keys.create({**fields, 'name': 'Media tools fixture B'})

            def request(method, path, value=None, token=None, content_type='application/json'):
                raw = json.dumps(value).encode() if isinstance(value, dict) else value
                c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=160)
                try:
                    c.request(method, path, raw, {'x-api-key': token or a['key'], 'Content-Type': content_type, 'X-WebCC-Tools': 'prompt-v1'} if path == '/v1/messages' else {'x-api-key': token or a['key'], 'Content-Type': content_type})
                    r = c.getresponse(); raw = r.read(2 * 1024 * 1024)
                    return r.status, raw
                finally:
                    c.close()

            tools = [
                {'name': 'read_media', 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
                {'name': 'save_result', 'description': 'Save the count or sum calculated from the actual attachment.', 'strict': True,
                 'input_schema': {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value'], 'additionalProperties': False}}]
            for filename, mime, raw, expected, in_result in [
                ('allocation.pdf', 'application/pdf', (Path(__file__).parent/'fixtures/allocation.pdf').read_bytes(), 120, False),
                ('blocks.png', 'image/png', (Path(__file__).parent/'fixtures/blocks.png').read_bytes(), 3, False),
                ('tool-allocation.pdf', 'application/pdf', (Path(__file__).parent/'fixtures/allocation.pdf').read_bytes(), 120, True)]:
                boundary = 'webcc-' + secrets.token_hex(12)
                body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode()+raw+(f'\r\n--{boundary}--\r\n').encode()
                status,response=request('POST','/v1/files',body,content_type='multipart/form-data; boundary='+boundary)
                assert status==200
                file=json.loads(response); path='/v1/files/'+file['id']
                assert request('GET',path+'/content')==(200,raw)
                media={'type':'image' if mime.startswith('image/') else 'document','source':{'type':'file','file_id':file['id']}}
                question='Count the colored rectangles in the image.' if mime.startswith('image/') else 'Add North and South allocations from the supplied PDF.'
                instruction=question+' Call save_result with the actual integer value. Do not guess from the filename.'
                messages=[{'role':'user','content':[media,{'type':'text','text':instruction}]}]
                if in_result:
                    messages=[{'role':'user','content':instruction},
                        {'role':'assistant','content':[{'type':'tool_use','id':'toolu_read_fixture','name':'read_media','input':{}}]},
                        {'role':'user','content':[{'type':'tool_result','tool_use_id':'toolu_read_fixture','content':[media]}]}]
                payload={'model':'webcc-prompt-v1','stream':False,'max_tokens':1024,'tools':tools,
                    'tool_choice':{'type':'tool','name':'save_result','disable_parallel_tool_use':True},'messages':messages}
                dispatched=sum(manager.dispatches.values())
                denied=request('POST','/v1/messages',payload,token=b['key']);assert denied[0]==404,denied
                assert sum(manager.dispatches.values())==dispatched
                status,response=request('POST','/v1/messages',payload); assert status==200,(status,response)
                first=json.loads(response); calls=[b for b in first['content'] if b['type']=='tool_use']
                assert len(calls)==1 and calls[0]['name']=='save_result' and calls[0]['input']=={'value':expected},first
                saved=data/(filename+'.json');saved.write_text(json.dumps(calls[0]['input']));actual=json.loads(saved.read_text())
                follow={**payload,'tool_choice':{'type':'none'},'messages':messages+[
                    {'role':'assistant','content':first['content']},
                    {'role':'user','content':[{'type':'tool_result','tool_use_id':calls[0]['id'],'content':json.dumps({'saved':actual['value']})},
                                             {'type':'text','text':'Reply only with the saved integer.'}]}]}
                status,response=request('POST','/v1/messages',follow);assert status==200,(status,response)
                final=json.loads(response); text=''.join(b.get('text','') for b in final['content']).strip()
                assert text==str(expected),final
                assert request('DELETE',path)[0]==200
                dispatched=sum(manager.dispatches.values())
                assert request('POST','/v1/messages',payload)[0]==404
                assert sum(manager.dispatches.values())==dispatched and manager.total==0
                report['checks'].append({'file':filename,'media_in_tool_result':in_result,'tool_input':calls[0]['input'],
                    'real_saved_value':actual['value'],'real_model_final':text,'cross_key_denied':True,'deleted_reference_denied_before_inference':True})
                print('MEDIA_CASE_PASS',filename,flush=True)
            report['model_requests']=6
            report['pass'] = True
        finally:
            if server:
                server.shutdown(); server.server_close()
            run('docker', 'rm', '-f', worker, check=False)
            report['production_registry_unchanged'] = production.read_bytes() == original
            report['temporary_container_removed'] = run('docker', 'inspect', worker, check=False).returncode != 0
    report['temporary_directory_removed'] = not root.exists()
    output = Path('/var/lib/clewdr-manager/media-candidate-result.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)); output.chmod(0o600)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
