"""Server-only isolated candidate sharing the production account lease limit."""
import copy
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
from dotenv import dotenv_values

ROOT = Path('/var/lib/webcc-cluster/repair-candidate')
APP = ROOT / 'app'
os.umask(0o077)
sys.path.insert(0, str(APP))
for path in ('/etc/clewdr-manager.env', '/var/lib/webcc-cluster/runtime-private.env'):
    os.environ.update({k: v for k, v in dotenv_values(path).items() if v is not None})
from cluster_state import ClusterState
from manager import Manager, Problem, Server

live = ClusterState(os.environ['MANAGER_DATABASE_URL'])
test = json.loads(Path('/var/lib/webcc-cluster/runtime-candidate/test-env.json').read_text())
assert test['WEBCC_CLUSTER_TEST_URL'] != os.environ['MANAGER_DATABASE_URL']
assert test['WEBCC_CLUSTER_TEST_REDIS'] != os.environ['MANAGER_REDIS_URL']
os.environ.update(MANAGER_DATABASE_URL=test['WEBCC_CLUSTER_TEST_URL'], MANAGER_REDIS_URL=test['WEBCC_CLUSTER_TEST_REDIS'],
    CLEWDR_PASSWORD=secrets.token_hex(32), CLEWDR_ADMIN_PASSWORD=secrets.token_hex(32),
    MANAGER_PORT='18296', MANAGER_RUNTIME_ENABLED='true', MANAGER_WEB_TOOLS_ENABLED='true', MANAGER_NODE_ID='node-1')
template = json.loads(Path('/var/lib/webcc-cluster/office-template.json').read_text())['build']
os.environ['MANAGER_E2B_TEMPLATE'] = template['template_id']
manager = Manager(ROOT / 'data', os.environ.get('MANAGER_IMAGE', ''), os.environ['CLEWDR_PASSWORD'], os.environ['CLEWDR_ADMIN_PASSWORD'])
with manager.cluster.pool.connection() as db:
    assert db.execute("SELECT count(*) FROM procrastinate_jobs WHERE status='doing'").fetchone()[0] == 0
    db.execute('TRUNCATE webcc_entities,webcc_occupancy,webcc_tasks,webcc_skills,files,procrastinate_jobs CASCADE')
manager.refresh()
manager.state['accounts'] = {k: copy.deepcopy(a) for k, a in live.load()['accounts'].items() if a['status'] == 'ready'}
manager.save()
# Candidate-only trace uses synthetic test requests and never production traffic.
from web_tools.streaming import ToolStream
original_feed = ToolStream.feed
def traced_feed(self, value):
    try:
        return original_feed(self, value)
    except Exception as error:
        trace = ROOT / 'traces'
        trace.mkdir(exist_ok=True)
        (trace / (secrets.token_hex(8) + '.json')).write_text(json.dumps({
            'error': str(error), 'raw': self.raw.decode(errors='replace'), 'request': self.request}, ensure_ascii=False))
        raise
ToolStream.feed = traced_feed
import document_citations
original_citations = document_citations.messages
def traced_citations(*args, **kwargs):
    try:
        return original_citations(*args, **kwargs)
    except Exception as error:
        import traceback
        (ROOT / 'citation-error.txt').write_text(traceback.format_exc())
        raise
document_citations.messages = traced_citations
leases, lock = {}, threading.Lock()
original_acquire, original_release = manager.acquire, manager.release

def acquire(*args, **kwargs):
    account = original_acquire(*args, **kwargs)
    token = live.reserve(account['id'], 'repair-candidate', 4)
    if not token:
        original_release(account)
        raise Problem(429, 'Production shared account capacity is busy')
    with lock:
        leases[account['id']] = token
    return account

def release(account):
    original_release(account)
    with lock:
        token = leases.pop(account['id'], None)
    if token:
        live.release(account['id'], token)

manager.acquire, manager.release = acquire, release
private = {'base_url': 'http://127.0.0.1:18296', 'admin': os.environ['CLEWDR_ADMIN_PASSWORD'],
           'api_key': os.environ['CLEWDR_PASSWORD'], 'database_url': os.environ['MANAGER_DATABASE_URL'],
           'accounts': list(manager.state['accounts']), 'template_id': os.environ['MANAGER_E2B_TEMPLATE']}
(ROOT / 'private.json').write_text(json.dumps(private))
worker = subprocess.Popen([sys.executable, '-m', 'runtime_queue'], cwd=APP, stdout=open(ROOT/'worker.log','a'), stderr=subprocess.STDOUT)
server = Server(('127.0.0.1', 18296), manager)
threading.Thread(target=server.serve_forever, daemon=True).start()
print('candidate_ready', flush=True)
stopped = threading.Event()
for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, lambda *_: stopped.set())
try:
    stopped.wait()
finally:
    server.shutdown(); server.server_close()
    worker.terminate(); worker.wait(timeout=20)
    for account, token in list(leases.items()):
        live.release(account, token)
    manager.cluster.pool.close(); live.pool.close()
