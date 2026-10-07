"""Real SDK tests against a temporary copy of the main gateway on the server."""
import json
import secrets
import tempfile
import threading
import time
from pathlib import Path
import anthropic
from manager import Manager, Server
from experiments.history_cases import TOOLS, run_history_case
from web_tools.api import MODEL


def run_gateway_case(account):
    with tempfile.TemporaryDirectory(prefix='webcc-sdk-manager-') as data:
        manager = Manager(data, account['image'], secrets.token_hex(24), secrets.token_hex(24))
        manager.web_tools_enabled = True
        manager.state['accounts'] = {account['id']: {**account, 'port': 19092}}
        manager.save()
        server = Server(('127.0.0.1', 0), manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        streamed = account['name'] == 'cc1'
        try:
            with anthropic.Anthropic(api_key=manager.api_key, base_url=f'http://127.0.0.1:{server.server_port}',
                    default_headers={'X-WebCC-Tools': 'prompt-v1'}, max_retries=0, timeout=60,
                    http_client=anthropic.DefaultHttpxClient(trust_env=False)) as client:
                def send(prompt):
                    # Use the original validated history, not its flattened prompt, at the HTTP boundary.
                    payload = json.loads(prompt.split('\n', 1)[1])
                    options = dict(model=MODEL, max_tokens=4096, tools=payload['tools'],
                                   messages=payload['history'], tool_choice=payload['tool_choice'])
                    begin = time.monotonic()
                    if streamed:
                        with client.messages.stream(**options) as stream:
                            message = stream.get_final_message()
                    else:
                        message = client.messages.create(**options)
                    record = message.model_dump(exclude_none=True)
                    return {'status': 200, 'message': record, 'seconds': round(time.monotonic() - begin, 2),
                            'sdk_streamed': streamed, 'model': record['model'], 'usage': record['usage']}
                result = run_history_case(send)
                result['sdk_streamed'] = streamed
                result['sdk_version'] = anthropic.__version__
                dispatched = sum(manager.dispatches.values())
                bad = [{'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'foreign', 'content': 'fake'}]}]
                try:
                    client.messages.create(model=MODEL, max_tokens=128, tools=TOOLS, messages=bad)
                    result['bad_id_rejected'] = False
                except anthropic.BadRequestError as error:
                    result['bad_id_rejected'] = error.status_code == 400
                result['bad_id_not_dispatched'] = sum(manager.dispatches.values()) == dispatched
                result['slots_released'] = manager.total == 0 and manager.waiting == 0
                result['account_still_ready'] = manager.get_account(account['id'])['status'] == 'ready'
                result['pass'] &= all(result[k] for k in ['bad_id_rejected', 'bad_id_not_dispatched', 'slots_released', 'account_still_ready'])
                return result
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)
