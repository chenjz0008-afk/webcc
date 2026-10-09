"""Server-only relay acceptance using a reserved web account and isolated database."""
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uvicorn
from cluster_state import ClusterState
from manager import Manager, Server
from node_agent import NodeAgent


def command(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def main():
    config = dict(line.split('=', 1) for line in Path('/var/lib/webcc-cluster/candidate.env').read_text().splitlines())
    live = ClusterState(config['MANAGER_DATABASE_URL'])
    state = live.load()
    account = next(a for a in state['accounts'].values() if a['name'] == 'ccb9' and a['status'] == 'ready')
    lease = live.reserve(account['id'], 'relay-proof', 4)
    if not lease: raise RuntimeError('Test account is busy; no request dispatched')
    database = 'webcc_relay_' + secrets.token_hex(6)
    worker = 'webcc-relay-proof'
    agent_server = gateway = agent_manager = manager = None
    report = {'candidate_only': True, 'physical_hosts': 1, 'checks': []}
    try:
        command('docker', 'exec', 'webcc-postgres', 'createdb', '-U', 'webcc', database)
        os.environ.update(MANAGER_DATABASE_URL=config['MANAGER_DATABASE_URL'].rsplit('/', 1)[0]+'/'+database,
            MANAGER_REDIS_URL=config['MANAGER_REDIS_URL'].rsplit('/', 1)[0]+'/14',
            MANAGER_NODE_ID='node-1', MANAGER_WEB_TOOLS_ENABLED='true')
        with tempfile.TemporaryDirectory(prefix='webcc-relay-proof-') as temporary:
            root=Path(temporary); directory=root/'config'; directory.mkdir(mode=0o700)
            for path in Path(account['directory']).glob('*.toml'): shutil.copy2(path, directory/path.name)
            command('docker', 'run', '-d', '--name', worker, '--network', 'webcc-'+account['id'], '--dns', '127.0.0.1',
                '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1', '--log-driver', 'none', '--memory', '256m', '--cpus', '1',
                '--pids-limit', '64', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '-p', '127.0.0.1:19099:8484', '-v', str(directory)+':/etc/clewdr', account['image'])
            for _ in range(30):
                c=http.client.HTTPConnection('127.0.0.1',19099,timeout=2)
                try:
                    c.request('GET','/api/version'); r=c.getresponse(); r.read()
                    if r.status==200: break
                except OSError: pass
                finally: c.close()
                time.sleep(1)
            else: raise RuntimeError('Worker startup failed')
            data=root/'agent'; data.mkdir(mode=0o700)
            agent_manager=Manager(data,account['image'],secrets.token_hex(32),secrets.token_hex(32))
            agent_manager.state['accounts'][account['id']]={**account,'node_id':'node-1','port':19099,'directory':str(directory)}
            agent_manager.save()
            token=secrets.token_hex(32)
            listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(16)
            port=listener.getsockname()[1]
            agent_server=uvicorn.Server(uvicorn.Config(NodeAgent(agent_manager,token).app(),log_level='error',access_log=False,limit_concurrency=32))
            thread=threading.Thread(target=lambda:agent_server.run(sockets=[listener]),daemon=True); thread.start()
            for _ in range(100):
                if agent_server.started: break
                time.sleep(.05)
            else: raise RuntimeError('Agent startup failed')
            nodes=root/'nodes.json';nodes.write_text(json.dumps({'node-1':{'url':'http://127.0.0.1:'+str(port),'token':token}}));nodes.chmod(0o600)
            os.environ.update(MANAGER_NODE_ID='edge-proof',MANAGER_NODES_FILE=str(nodes))
            manager=Manager(root/'gateway',account['image'],secrets.token_hex(32),secrets.token_hex(32))
            gateway=Server(('127.0.0.1',0),manager); threading.Thread(target=gateway.serve_forever,daemon=True).start()
            key=manager.api_keys.create({'name':'relay proof','accounts':[account['id']],'scopes':['messages','experimental_tools'],'rpm':60})
            def request(payload, tools=False):
                c=http.client.HTTPConnection('127.0.0.1',gateway.server_port,timeout=160)
                try:
                    headers={'x-api-key':key['key'],'Content-Type':'application/json','User-Agent':'caller-private-host-marker','X-Forwarded-For':'185.0.0.1'}
                    if tools: headers['X-WebCC-Tools']='prompt-v1'
                    c.request('POST','/v1/messages',json.dumps(payload),headers)
                    response=c.getresponse();raw=response.read(2*1024*1024)
                    assert response.status==200,(response.status,raw[:300])
                    assert b'caller-private-host-marker' not in raw
                    return raw
                finally:c.close()
            base={'model':'claude-sonnet-4-6','max_tokens':128,'messages':[{'role':'user','content':'Add 37 and 83. Reply only the integer.'}]}
            result=json.loads(request(base));text=''.join(b.get('text','') for b in result['content']).strip();assert text=='120',result
            report['checks'].append({'real_messages_reply':text})
            tool={'name':'save_total','strict':True,'input_schema':{'type':'object','properties':{'total':{'const':120}},'required':['total'],'additionalProperties':False}}
            result=json.loads(request({**base,'model':'webcc-prompt-v1','tools':[tool],'tool_choice':{'type':'tool','name':'save_total'}},True))
            call=next(b for b in result['content'] if b['type']=='tool_use');assert call['input']=={'total':120}
            report['checks'].append({'real_tool':call['name'],'input':call['input']})
            stream=request({**base,'stream':True});assert b'event: message_stop' in stream and b'120' in stream
            report['checks'].append({'real_sse_terminal':True})
            for _ in range(100):
                with manager.cluster.pool.connection() as db: occupied=db.execute('SELECT count(*) FROM webcc_occupancy').fetchone()[0]
                if not occupied and manager.total == 0: break
                time.sleep(.05)
            report['final_occupancy'] = occupied
            report['final_gateway_inflight'] = manager.total
            assert not occupied and manager.total==0, (occupied, manager.total)
            report.update(pass_=True, isolated_occupancy_released=True, production_worker_unchanged=True)
    finally:
        if gateway:gateway.shutdown();gateway.server_close()
        if agent_server:agent_server.should_exit=True;thread.join(timeout=10)
        if manager:manager.cluster.pool.close()
        command('docker','rm','-f',worker,check=False)
        command('docker','exec','webcc-postgres','dropdb','-U','webcc','--force',database,check=False)
        live.release(account['id'],lease);live.pool.close()
        Path('/var/lib/clewdr-manager/node-relay-proof.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report),flush=True)


if __name__=='__main__':main()
