import http.client
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch
import test_manager as base
Worker = base.Worker
from web_tools.api import MODEL

TOOLS = [{'name': 'read', 'input_schema': {'type': 'object',
          'properties': {'id': {'enum': ['A']}}, 'required': ['id'], 'additionalProperties': False}}]
OUTPUT = {'calls': [{'name': 'read', 'input': {'id': 'A'}}], 'text': ''}


def raw_output(output=OUTPUT):
    return json.dumps({'content': [{'type': 'text', 'text': json.dumps(output)}],
                       'usage': {'input_tokens': 40, 'output_tokens': 20}}).encode()


class WebGatewayTests(unittest.TestCase):
    tearDown = base.ManagerTests.tearDown
    request = base.ManagerTests.request
    assert_idle = base.ManagerTests.assert_idle
    def setUp(self):
        base.ManagerTests.setUp(self)
        self.manager.web_tools_enabled = True
        Worker.replies['Bearer ' + self.manager.get_account(self.identity)['key']] = (200, raw_output())

    def payload_web(self, stream=False):
        return {'model': MODEL, 'tools': TOOLS, 'max_tokens': 1024,
                'messages': [{'role': 'user', 'content': 'Read A'}], 'stream': stream}

    def web(self, body=None):
        return self.request('POST', '/v1/messages', body or self.payload_web(), extra={'X-WebCC-Tools': 'prompt-v1'})

    def test_media_admission_rejected_before_dispatch(self):
        body=self.payload_web();body['messages'][0]['content']=[{'type':'document','source':{'type':'text','data':'fixture'}}]
        with patch('web_tools.gateway.MEDIA_SLOTS') as slots:
            slots.acquire.return_value=False
            self.assertEqual(self.web(body)[0],429)
            slots.release.assert_not_called()
        self.assertFalse(Worker.seen);self.assert_idle()

    def test_media_admission_released_after_validation_failure(self):
        body=self.payload_web();body['messages'][0]['content']=[{'type':'image','source':{'type':'url','url':'http://127.0.0.1'}}]
        released = threading.Event()
        with patch('web_tools.gateway.MEDIA_SLOTS') as slots:
            slots.acquire.return_value=True
            slots.release.side_effect = released.set
            self.assertEqual(self.web(body)[0],400)
            self.assertTrue(released.wait(1))
            slots.release.assert_called_once()
        self.assertFalse(Worker.seen);self.assert_idle()

    def test_opt_in_and_usage(self):
        status, headers, data = self.web()
        self.assertEqual(status, 200)
        message = json.loads(data)
        self.assertEqual(message['model'], MODEL)
        self.assertEqual(message['content'][0]['name'], 'read')
        self.assertEqual(message['usage'], {'input_tokens': 40, 'output_tokens': 20})
        upstream = json.loads(Worker.seen[-1]['body'])
        self.assertNotIn('tools', upstream)
        self.assertTrue(upstream['stream'])
        self.assertNotIn('X-WebCC-Tools', Worker.seen[-1]['headers'])
        self.assert_idle()

    def test_disabled_mode(self):
        self.manager.web_tools_enabled = False
        self.assertEqual(self.web()[0], 400)
        self.assertEqual(Worker.seen, [])
        self.assert_idle()

    def test_client_identity_headers_not_forwarded(self):
        private = {'User-Agent': 'local-host-marker', 'X-Forwarded-For': '203.0.113.7',
                   'X-Real-IP': '203.0.113.7', 'Forwarded': 'for=203.0.113.7',
                   'X-Client-Hostname': 'local-host-marker'}
        for experimental in (False, True):
            payload = self.payload_web() if experimental else {
                'model': 'claude-sonnet-4-6', 'max_tokens': 32,
                'messages': [{'role': 'user', 'content': 'Hi'}]}
            headers = {**private, **({'X-WebCC-Tools': 'prompt-v1'} if experimental else {})}
            self.assertEqual(self.request('POST', '/v1/messages', payload, extra=headers)[0], 200)
            upstream = Worker.seen[-1]
            received = {key.lower() for key in upstream['headers']}
            self.assertTrue(received.isdisjoint(key.lower() for key in private))
            for value in private.values():
                self.assertNotIn(value.encode(), upstream['body'])
            self.assert_idle()

    def test_invalid_request_no_account_failure(self):
        for fields in [{'thinking': {'type': 'enabled'}}, {'model': 'claude-sonnet-4-6'},
                       {'tools': [{'name': 'read', 'strict': 'true', 'input_schema': {'type': 'object'}}]},
                       {'messages': [{'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'foreign'}]}]}]:
            self.assertEqual(self.web({**self.payload_web(), **fields})[0], 400)
        self.assertFalse(Worker.seen)
        self.assertEqual(self.manager.get_account(self.identity)['status'], 'ready')
        self.assert_idle()

    def test_buffered_sse_complete(self):
        status, headers, data = self.web(self.payload_web(True))
        self.assertEqual(status, 200)
        self.assertIn('buffered', headers['X-WebCC-Adapter'])
        self.assertIn(b'input_json_delta', data)
        self.assertIn(b'event: message_stop', data)
        self.assert_idle()

    def test_bad_model_output_not_quarantined(self):
        account = self.manager.get_account(self.identity)
        Worker.replies['Bearer ' + account['key']] = (200, raw_output({'calls': [], 'text': ''}))
        for _ in range(4):
            status, _, data = self.web()
            self.assertEqual(status, 502)
            self.assertNotIn(account['key'].encode(), data)
        self.assertEqual(account['status'], 'ready')
        self.assertEqual(account.get('consecutive_failures', 0), 0)
        self.assert_idle()

    def test_retry_other_account_once_before_tools(self):
        second = self.manager.add({'name': 'second', 'sessionKey': 'sk-ant-sid02-' + 'B' * 100 + '-ABCDEF' + 'AA', 'proxy': '127.0.0.1:1080:u:p'})
        first = self.manager.get_account(self.identity)
        Worker.replies['Bearer ' + first['key']] = (500, b'{"error":"secret"}')
        Worker.replies['Bearer ' + self.manager.get_account(second)['key']] = (200, raw_output())
        status, _, data = self.web()
        self.assertEqual(status, 200)
        self.assertEqual(len(Worker.seen), 2)
        self.assertEqual(first['status'], 'ready')
        self.assertEqual(first['consecutive_failures'], 1)
        self.assertEqual(len(json.loads(data)['content']), 1)
        self.assert_idle()

    def test_transport_diagnostics_do_not_log_upstream_secrets(self):
        with patch('upstream_message.read', side_effect=ConnectionResetError('private-upstream-detail')), patch('builtins.print') as output:
            status, _, data = self.web()
        self.assertEqual(status, 502)
        records = [json.loads(call.args[0]) for call in output.call_args_list
                   if call.args and isinstance(call.args[0], str) and call.args[0].startswith('{')]
        failures = [record for record in records if record.get('event') == 'tool_upstream_failure']
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['failure'], 'ConnectionResetError')
        self.assertEqual(failures[0]['stage'], 'upstream_read')
        self.assertFalse(failures[0]['canceled'])
        self.assertNotIn('private-upstream-detail', json.dumps(records))
        self.assertNotIn('private-upstream-detail', data.decode())
        self.assertNotIn(self.manager.get_account(self.identity)['key'], json.dumps(records))
        self.assert_idle()

    def test_standard_stream_disconnect_after_tool_is_not_replayed(self):
        self.manager.add({'name': 'second', 'sessionKey': 'sk-ant-sid02-' + 'B' * 100 + '-ABCDEF' + 'AA',
                          'proxy': '127.0.0.1:1080:u:p'})
        def interrupted(response, thinking=False, on_event=None):
            on_event({'type': 'message_start', 'message': {'model': 'upstream', 'usage': {'input_tokens': 1, 'output_tokens': 0}}})
            on_event({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': json.dumps(OUTPUT)}})
            raise ConnectionResetError('private-upstream-detail')
        body = {**self.payload_web(True), 'model': 'claude-sonnet-4-6'}
        with patch('upstream_message.read', side_effect=interrupted):
            status, _, raw = self.request('POST', '/v1/messages', body)
        self.assertEqual(status, 200)
        frames = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b'data: ')]
        calls = [f for f in frames if f['type'] == 'content_block_start' and f['content_block']['type'] == 'tool_use']
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(Worker.seen), 1)
        self.assertEqual(frames[-1]['error']['code'], 'upstream_stream_interrupted')
        self.assertNotIn('message_stop', [f['type'] for f in frames])
        self.assertNotIn(b'private-upstream-detail', raw)
        self.assert_idle()

    def test_strict_stream_delivers_progress_before_upstream_finishes(self):
        waiting, finish = threading.Event(), threading.Event()
        value={'text':'Preparing.','calls':OUTPUT['calls']}
        text=json.dumps(value)
        split=text.index('[{')+1
        def generation(response, thinking=False, on_event=None):
            on_event({'type':'message_start','message':{'model':'upstream','usage':{'input_tokens':1,'output_tokens':0}}})
            on_event({'type':'content_block_delta','delta':{'type':'text_delta','text':text[:split]}})
            waiting.set()
            if not finish.wait(4):raise TimeoutError()
            on_event({'type':'content_block_delta','delta':{'type':'text_delta','text':text[split:]}})
            return raw_output(value)
        body={**self.payload_web(True),'model':'claude-sonnet-4-6','tools':[{**TOOLS[0],'strict':True}]}
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2)
        with patch('upstream_message.read',side_effect=generation):
            try:
                connection.request('POST','/v1/messages',json.dumps(body),
                    {'Authorization':'Bearer api-password','Content-Type':'application/json'})
                response=connection.getresponse()
                self.assertTrue(waiting.wait(1))
                data=b''
                while b'Preparing.' not in data:data+=response.read1(65536)
                self.assertNotIn(b'"type": "tool_use"',data)
                self.assertFalse(finish.is_set())
                finish.set()
                data+=response.read()
                self.assertIn(b'"type": "tool_use"',data)
                self.assertIn(b'event: message_stop',data)
            finally:
                finish.set();connection.close()
        self.assert_idle()

    def test_stream_error_without_tool_blocks(self):
        account = self.manager.get_account(self.identity)
        Worker.replies['Bearer ' + account['key']] = (200, raw_output({'calls': [], 'text': ''}))
        status, _, data = self.web(self.payload_web(True))
        self.assertEqual(status, 200)
        self.assertIn(b'event: error', data)
        self.assertNotIn(b'content_block_start', data)
        self.assertNotIn(b'message_stop', data)
        self.assert_idle()

    def test_disconnect_cancels_slow_generation(self):
        started, finish = threading.Event(), threading.Event()
        original = Worker.do_POST
        def slow(worker):
            started.set()
            finish.wait(4)
            try:
                original(worker)
            except OSError:
                pass
        with patch.object(Worker, 'do_POST', slow):
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
            connection.request('POST', '/v1/messages', json.dumps(self.payload_web(True)), {'Authorization': 'Bearer api-password', 'X-WebCC-Tools': 'prompt-v1', 'Content-Type': 'application/json'})
            response = connection.getresponse()
            self.assertTrue(started.wait(1))
            connection.sock.shutdown(socket.SHUT_RDWR)
            response.close(); connection.close()
            deadline = time.monotonic() + 2
            while self.manager.total and time.monotonic() < deadline:
                time.sleep(.05)
            finish.set()
            self.assert_idle()
            self.assertEqual(self.manager.get_account(self.identity)['status'], 'ready')


    def test_alias_requires_header(self):
        self.assertEqual(self.request('POST', '/v1/messages', self.payload_web())[0], 400)
        self.assertFalse(Worker.seen)
        self.assert_idle()

    def test_invalid_schema_history_no_dispatch(self):
        payload = self.payload_web()
        payload['messages'].extend([
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'x', 'name': 'read', 'input': {'id': 'B'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'x', 'content': 'bad'}]}])
        self.assertEqual(self.web(payload)[0], 400)
        self.assertFalse(Worker.seen)
        self.assert_idle()

    def test_repeated_500_threshold(self):
        account = self.manager.get_account(self.identity)
        Worker.replies['Bearer ' + account['key']] = (500, b'{"error":"secret"}')
        for index in range(3):
            self.assertEqual(self.web()[0], 500)
            self.assertEqual(account['status'], 'quarantined' if index == 2 else 'ready')
        self.assert_idle()

    def test_cookie_pool_cools_without_permanent_quarantine_and_switches_account(self):
        first = self.manager.get_account(self.identity)
        second = self.manager.add({'name': 'second', 'sessionKey': base.SESSION.replace('A' * 100, 'B' * 100), 'proxy': '127.0.0.1:1080:user:pass'})
        Worker.replies['Bearer '+first['key']] = (500, b'{"error":{"type":"no_cookie_available","message":"No cookie available","code":500}}')
        Worker.replies['Bearer '+self.manager.get_account(second)['key']] = (200, raw_output())
        for _ in range(3):
            first['cooldown_until'] = 0
            self.manager.save()
            self.assertEqual(self.web()[0], 200)
            self.assertEqual(first['status'], 'ready')
            self.assertEqual(first.get('consecutive_failures', 0), 0)
            self.assertGreater(first['cooldown_until'], time.time())
        self.assert_idle()

    def test_no_cookie_error_is_specific_and_public_response_is_safe(self):
        first = self.manager.get_account(self.identity)
        Worker.replies['Bearer '+first['key']] = (500, b'{"error":{"type":"no_cookie_available","message":"worker-secret"}}')
        status, _, raw = self.web()
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(raw)['error']['code'], 'cookie_pool_unavailable')
        self.assertNotIn(b'worker-secret', raw)
        self.assertEqual(first['status'], 'ready')
        self.assert_idle()

    def test_queued_disconnect_releases_waiter(self):
        held = self.manager.acquire()
        self.manager.queue_seconds = 5
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.request('POST', '/v1/messages', json.dumps(self.payload_web()), {'Authorization': 'Bearer api-password', 'X-WebCC-Tools': 'prompt-v1', 'Content-Type': 'application/json'})
            deadline = time.monotonic() + 1
            while not self.manager.waiting and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(self.manager.waiting, 1)
            connection.sock.shutdown(socket.SHUT_RDWR);connection.close()
            deadline = time.monotonic() + 1
            while self.manager.waiting and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(self.manager.waiting, 0)
            self.assertEqual(self.manager.total, 1)
            self.assertFalse(Worker.seen)
        finally:
            connection.close();self.manager.release(held)
        self.assert_idle()

    def test_strict_flag_is_explicit_local_validation(self):
        payload = self.payload_web();payload['tools'] = [{**TOOLS[0], 'strict': True}]
        status, headers, raw = self.web(payload)
        self.assertEqual(status, 200)
        self.assertIn('no-native-strict', headers['X-WebCC-Adapter'])
        self.assertEqual(json.loads(raw)['content'][0]['input'], {'id': 'A'})
        account = self.manager.get_account(self.identity)
        Worker.replies['Bearer ' + account['key']] = (200, raw_output({'calls': [{'name': 'read', 'input': {'id': 'outside'}}], 'text': ''}))
        self.assertEqual(self.web(payload)[0], 502)
        self.assertEqual(account['status'], 'ready')
        self.assert_idle()
