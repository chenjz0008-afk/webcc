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


def run_typescript_case(account, node, script, streamed, tls_context=None, ca_file=None, file_documents=False, scoped_key=False, document_review=False, basic_capabilities=False):
    with tempfile.TemporaryDirectory(prefix='webcc-ts-manager-') as data:
        manager = Manager(data, account['image'], secrets.token_hex(24), secrets.token_hex(24))
        manager.web_tools_enabled = True
        manager.state['accounts'] = {account['id']: {**account, 'port': 19096}}
        manager.save()
        credential = manager.api_keys.create({'name': 'SDK verification', 'accounts': [account['id']],
                         'scopes': ['messages', 'experimental_tools'], 'rpm': 60}) if scoped_key else None
        server = Server(('127.0.0.1', 0), manager)
        if tls_context:
            server.socket = tls_context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            trace_ids = []
            def send_payload(payload, trust=True):
                request = {'baseURL': f'{"https" if tls_context else "http"}://127.0.0.1:{server.server_port}',
                           'apiKey': credential['key'] if credential else manager.api_key,
                           'streamed': streamed, 'model': MODEL, 'max_tokens': 4096, **payload}
                env = {'PATH': '/usr/bin:/bin'}
                if ca_file and trust:
                    env['NODE_EXTRA_CA_CERTS'] = str(ca_file)
                process = subprocess.run([str(node), str(script)], input=json.dumps(request), env=env,
                                         capture_output=True, text=True, timeout=100)
                result = json.loads(process.stdout)
                if trust and result['status']:
                    trace_ids.append(result.get('sdk_request_id'))
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
            if basic_capabilities:
                from experiments.basic_cases import run_basic_cases
                result = run_basic_cases(send_payload, Path(script).parent / 'fixtures')
            elif document_review:
                from experiments.document_cases import run_document_case
                renderer = Path(script).with_name('render_markdown.mjs')
                def render(documents):
                    process = subprocess.run([str(node), str(renderer)], input=json.dumps(documents),
                                             env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, timeout=10, check=True)
                    return json.loads(process.stdout)
                result = run_document_case(send, Path(data) / 'review-documents', render if renderer.exists() else None)
            else:
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
            if credential:
                manager.api_keys.revoke(credential['id'])
                rejected = send_payload({'tools': TOOLS, 'messages': [{'role': 'user', 'content': 'Hi'}]})
                result['revoked_key_rejected'] = rejected['status'] == 401 and rejected.get('error_kind') == 'authentication_error'
                result['revocation_no_dispatch'] = sum(manager.dispatches.values()) == dispatched
                result['scoped_account_dispatches_only'] = set(manager.dispatches) == {account['id']}
                result['pass'] &= all(result[k] for k in ['revoked_key_rejected', 'revocation_no_dispatch', 'scoped_account_dispatches_only'])
            result['sdk_streamed'] = streamed
            result['sdk_request_ids_present'] = all(isinstance(value, str) and value for value in trace_ids)
            result['pass'] &= result['sdk_request_ids_present']
            result['verified_https'] = bool(tls_context and ca_file)
            if ca_file:
                result['untrusted_certificate_rejected_before_dispatch'] = tls_rejected
                result['pass'] &= tls_rejected
            result['pass'] &= all(result[k] for k in ['bad_id_rejected', 'bad_id_not_dispatched', 'slots_released'])
            return result
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)
