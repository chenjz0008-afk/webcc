"""Use the official JS SDK on the server; client identity stays synthetic."""
import json
import secrets
import subprocess
import tempfile
import threading
from pathlib import Path
from manager import Manager, Server
from experiments.history_cases import TOOLS, run_history_case
from web_tools.api import MODEL


def run_typescript_case(account, node, script, streamed, tls_context=None, ca_file=None, file_documents=False):
    with tempfile.TemporaryDirectory(prefix='webcc-ts-manager-') as data:
        manager = Manager(data, account['image'], secrets.token_hex(24), secrets.token_hex(24))
        manager.web_tools_enabled = True
        manager.state['accounts'] = {account['id']: {**account, 'port': 19096}}
        manager.save()
        server = Server(('127.0.0.1', 0), manager)
        if tls_context:
            server.socket = tls_context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            def send_payload(payload, trust=True):
                request = dict(baseURL=f'{"https" if tls_context else "http"}://127.0.0.1:{server.server_port}', apiKey=manager.api_key,
                               streamed=streamed, model=MODEL, max_tokens=4096, **payload)
                env = {'PATH': '/usr/bin:/bin'}
                if ca_file and trust:
                    env['NODE_EXTRA_CA_CERTS'] = str(ca_file)
                process = subprocess.run([str(node), str(script)], input=json.dumps(request), env=env,
                                         capture_output=True, text=True, timeout=100)
                result = json.loads(process.stdout)
                return result
            def send(prompt):
                payload = json.loads(prompt.split('\n', 1)[1])
                return send_payload(dict(tools=payload['tools'], messages=payload['history'],
                                         tool_choice=payload['tool_choice']))
            tls_rejected = True
            if ca_file:
                rejected = send_payload({'tools': TOOLS, 'messages': [{'role': 'user', 'content': 'Hi'}]}, trust=False)
                tls_rejected = rejected['status'] == 0 and sum(manager.dispatches.values()) == 0
            if file_documents:
                from experiments.markdown_documents import MarkdownDocuments
                store = MarkdownDocuments(Path(data) / 'documents')
            else:
                store = None
            result = run_history_case(send, store)
            if store:
                result['files_preserved'] = store.verify()
                result['pass'] &= result['files_preserved']
            dispatched = sum(manager.dispatches.values())
            bad = send_payload({'tools': TOOLS, 'messages': [{'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'foreign', 'content': 'fake'}]}]})
            result['bad_id_rejected'] = bad['status'] == 400
            result['bad_id_not_dispatched'] = sum(manager.dispatches.values()) == dispatched
            result['slots_released'] = manager.total == 0 and manager.waiting == 0
            result['sdk_streamed'] = streamed
            result['verified_https'] = bool(tls_context and ca_file)
            if ca_file:
                result['untrusted_certificate_rejected_before_dispatch'] = tls_rejected
                result['pass'] &= tls_rejected
            result['pass'] &= all(result[k] for k in ['bad_id_rejected', 'bad_id_not_dispatched', 'slots_released'])
            return result
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)
