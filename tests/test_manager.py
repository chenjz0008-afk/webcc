import http.client
import json
import os
import sys
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manager import Manager, Problem, Server, StreamState, normalize_proxy, parse_account_text, probe_proxy
import updater


SESSION = "sk-ant-sid02-" + "A" * 100 + "-ABCDEF" + "AA"


class Worker(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    status_code = 200
    seen = []
    replies = {}

    def log_message(self, *args):
        pass

    def reply(self, data, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.seen.append({"path": self.path, "headers": dict(self.headers), "body": b""})
        self.reply(json.dumps({"data": [{"id": "claude-sonnet-4-6"}]}).encode())

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        configured = self.replies.get(self.headers.get("Authorization"))
        if configured:
            self.reply(configured[1], configured[0], configured[2] if len(configured) > 2 else "application/json")
        elif self.status_code != 200:
            self.reply(b'{"error":"worker-secret"}', self.status_code)
        elif json.loads(body).get("stream"):
            data = b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n'
            self.reply(data, content_type="text/event-stream")
        else:
            self.reply(b'{"choices":[{"message":{"content":"OK"}}],"model":"claude-sonnet-4-6"}')


class FakeManager(Manager):
    def __init__(self, *args, **kwargs):
        self.containers = {}
        self.worker_port = None
        super().__init__(*args, **kwargs)

    def docker(self, *args, **kwargs):
        command = args[0]
        if command == "run":
            name = args[args.index("--name") + 1]
            self.containers[name] = {"State": {"Running": True, "Status": "running"},
                                      "Mounts": [{"Source": "/private/data", "Destination": "/etc/clewdr", "RW": True}], "Image": "sha256:image"}
            return name
        if command == "inspect":
            if args[1] not in self.containers:
                raise Problem(502, "Docker inspect failed")
            return json.dumps([self.containers[args[1]]])
        if command in {"stop", "start"}:
            value = self.containers[args[1]]
            value["State"] = {"Running": command == "start", "Status": "running" if command == "start" else "exited"}
        if command == "rm":
            self.containers.pop(args[1], None)
        if command == "image":
            return json.dumps([{"RepoDigests": [updater.IMAGE + "@sha256:" + "a" * 64]}])
        return ""

    def wait_ready(self, port):
        return "v0.13.5"

    def test_proxy(self, fields):
        normalize_proxy(fields.get("proxy", ""), fields.get("proxy_username", ""), fields.get("proxy_password", ""))
        return {"ok": True, "ip": "203.0.113.5", "latency_ms": 20, "checked_at": int(time.time())}

    def free_port(self):
        return self.worker_port or super().free_port()


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = FakeManager(self.temp.name, "official:version", "api-password", "admin-password", queue_seconds=.12)
        Worker.seen = []
        Worker.status_code = 200
        Worker.replies = {}
        self.worker = ThreadingHTTPServer(("127.0.0.1", 0), Worker)
        self.worker.daemon_threads = True
        threading.Thread(target=self.worker.serve_forever, daemon=True).start()
        self.manager.worker_port = self.worker.server_port
        self.identity = self.manager.add({"name": "test", "sessionKey": SESSION, "proxy": "127.0.0.1:1080:user:p@ss"})
        self.server = Server(("127.0.0.1", 0), self.manager)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.shutdown()
        self.worker.server_close()
        self.temp.cleanup()

    def request(self, method, path, body=None, key="api-password", extra=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", **(extra or {})}
        connection.request(method, path, None if body is None else json.dumps(body), headers)
        try:
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def payload(self, stream=False):
        return {"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "Hi"}], "stream": stream}

    def test_message_query_routes_preserve_body_and_query(self):
        for path in ("/v1/messages?beta=true", "/code/v1/messages?beta=true", "/v1/chat/completions?trace=one%20two"):
            with self.subTest(path=path):
                body = {**self.payload(), "tools": [{"name": "lookup", "input_schema": {"type": "object"}}]}
                status, _, _ = self.request("POST", path, body, key="", extra={"x-api-key": "api-password"})
                self.assertEqual(status, 200)
                self.assertEqual(Worker.seen[-1]["path"], path)
                self.assertEqual(json.loads(Worker.seen[-1]["body"]), body)
                self.assertNotIn("x-api-key", {k.lower(): v for k, v in Worker.seen[-1]["headers"].items()})
                self.assert_idle()

    def test_model_query_accepts_sdk_api_key(self):
        for path in ("/v1/models", "/v1/models?limit=1", "/code/v1/models?limit=1"):
            with self.subTest(path=path):
                status, _, body = self.request("GET", path, key="", extra={"x-api-key": "api-password"})
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["data"][0]["id"], "claude-sonnet-4-6")
                self.assertEqual(Worker.seen[-1]["path"], path)

    def test_query_route_requires_valid_credentials(self):
        for method, path in (("POST", "/v1/messages?beta=true"), ("GET", "/v1/models?limit=1")):
            with self.subTest(path=path):
                status, _, _ = self.request(method, path, self.payload() if method == "POST" else None,
                                             key="", extra={"x-api-key": "wrong"})
                self.assertEqual(status, 401)
        self.assertEqual(Worker.seen, [])

    def test_query_does_not_make_unknown_routes_public(self):
        self.assertEqual(self.request("POST", "/v1/not-an-endpoint?beta=true", self.payload())[0], 404)
        self.assertEqual(Worker.seen, [])

    def test_routing_rejects_external_request_targets(self):
        for path, expected in (("https://example.com/v1/messages?beta=true", 400),
                               ("//example.com/v1/messages", 404), ("/v1/messages#fragment", 400)):
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path, self.payload())[0], expected)
        self.assertEqual(Worker.seen, [])

    def test_admin_query_keeps_admin_authentication(self):
        self.assertEqual(self.request("GET", "/admin/accounts?view=list", key="",
                                      extra={"x-api-key": "api-password"})[0], 401)
        status, headers, _ = self.request("GET", "/admin/accounts?view=list", key="admin-password")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_proxy_rejects_incomplete_ipv4(self):
        with self.assertRaises(Problem):
            normalize_proxy("140.228.28:12324")
        self.assertEqual(normalize_proxy("140.228.28.66:12324"), "socks5://140.228.28.66:12324")

    def test_failed_proxy_creates_no_account(self):
        before = set(self.manager.state['accounts'])
        with patch.object(self.manager, 'test_proxy', side_effect=Problem(400, '代理连接超时')):
            with self.assertRaises(Problem):
                self.manager.add({'sessionKey': SESSION.replace('A' * 100, 'B' * 100), 'proxy': '1.2.3.4:1080'})
        self.assertEqual(set(self.manager.state['accounts']), before)
        self.assertEqual(len(list((Path(self.temp.name) / 'accounts').iterdir())), 1)

    def test_proxy_endpoint_authentication(self):
        self.assertEqual(self.request('POST', '/admin/proxy/test', {'proxy':'1.2.3.4:1080'})[0], 401)
        status, _, body = self.request('POST', '/admin/proxy/test', {'proxy':'1.2.3.4:1080'}, key='admin-password')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['ok'])

    def test_probe_proxy_credentials_not_in_arguments(self):
        from subprocess import CompletedProcess
        with patch('manager.subprocess.run', return_value=CompletedProcess([], 0, '{"ip":"203.0.113.8"}', '')) as run:
            self.assertEqual(probe_proxy('socks5://private-user:private-password@host:1080')['ip'], '203.0.113.8')
            self.assertNotIn('private-password', str(run.call_args.args))

    def test_probe_proxy_error_is_sanitized(self):
        from subprocess import CompletedProcess
        with patch('manager.subprocess.run', return_value=CompletedProcess([], 97, '', 'private-password')):
            with self.assertRaises(Problem) as caught:
                probe_proxy('socks5://u:private-password@host:1080')
            self.assertNotIn('private-password', str(caught.exception))

    def assert_idle(self):
        with self.manager.condition:
            self.manager.condition.wait_for(lambda: self.manager.total == 0, timeout=2)
        self.assertEqual(self.manager.total, 0)

    def test_proxy_encoding_and_rejection(self):
        self.assertEqual(normalize_proxy("1.2.3.4:1080:user:p@ss"), "socks5://user:p%40ss@1.2.3.4:1080")
        self.assertEqual(normalize_proxy("socks5://u:p%40ss@host:1080"), "socks5://u:p%40ss@host:1080")
        self.assertEqual(normalize_proxy("host:1080", "u@", "p:#"), "socks5://u%40:p%3A%23@host:1080")
        for value in ("1.2.3.4", "file:///etc/passwd", "http://host:80/path", "http://host:80\nheader"):
            with self.assertRaises(Problem):
                normalize_proxy(value)

    def test_import_only_file_header(self):
        result = parse_account_text(SESSION + '\nSock5\n1.2.3.4:1080:u:p\nUA\nmacOS20\nMozilla/5.0 Chrome/150\n[\n{"value":"unrelated-secret"}\n]')
        self.assertEqual(result["sessionKey"], SESSION)
        self.assertEqual(result["proxy"], "1.2.3.4:1080:u:p")
        self.assertNotIn("unrelated-secret", json.dumps(result))

    def test_private_config_and_user_preferences(self):
        account = self.manager.get_account(self.identity)
        path = Path(account["directory"]) / "clewdr.toml"
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        text = path.read_text()
        for value in ("max_retries = 5", "skip_restricted = true", "skip_first_warning = false", "skip_second_warning = false"):
            self.assertIn(value, text)
        self.assertIn(SESSION, text)

    def test_admin_snapshot_hides_secrets_and_preserves_status(self):
        status, _, body = self.request("GET", "/admin/accounts", key="admin-password")
        self.assertEqual(status, 200)
        self.assertNotIn(SESSION.encode(), body)
        self.assertNotIn(self.manager.get_account(self.identity)["key"].encode(), body)
        self.assertNotIn(b"p%40ss", body)
        account = json.loads(body)["accounts"][0]
        self.assertEqual(account["status"], "ready")
        self.assertEqual(account["docker_status"], "running")

    def test_api_key_cannot_manage_containers(self):
        self.assertEqual(self.request("GET", "/admin/accounts")[0], 401)
        self.assertEqual(self.request("POST", "/admin/accounts", {})[0], 401)

    def test_edit_metadata_keeps_worker_and_hides_email_password(self):
        path = '/admin/accounts/' + self.identity + '/details'
        container = self.manager.get_account(self.identity)['container']
        with patch.object(self.manager, 'docker', wraps=self.manager.docker) as docker:
            status, _, body = self.request('POST', path, {'email':'owner@example.com','email_password':'private-email-password','notes':'internal note','session_expires_at':int(time.time())+86400}, key='admin-password')
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)['restarted'])
            self.assertFalse(any(call.args[0] in {'stop','run','rm'} for call in docker.call_args_list))
        status, headers, body = self.request('GET', path, key='admin-password')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        details = json.loads(body)
        self.assertEqual(details['email_password'], 'private-email-password')
        self.assertEqual(details['proxy_password'], 'p@ss')
        self.assertEqual(details['sessionKey'], SESSION)
        _, _, snapshot = self.request('GET', '/admin/accounts', key='admin-password')
        self.assertNotIn(b'private-email-password', snapshot)
        self.assertNotIn(SESSION.encode(), snapshot)
        self.assertEqual(self.request('GET', path)[0], 401)
        restored = FakeManager(self.temp.name, 'official:version', 'api-password', 'admin-password')
        self.assertEqual(restored.get_account(self.identity)['email'], 'owner@example.com')
        self.assertEqual(restored.get_account(self.identity)['container'], container)

    def test_expired_session_is_excluded_and_not_resumable(self):
        account = self.manager.get_account(self.identity)
        account['session_expires_at'] = int(time.time())-1
        self.assertEqual(self.manager.snapshot()['accounts'][0]['status'], 'expired')
        self.assertEqual(self.request('POST', '/v1/chat/completions', self.payload())[0], 503)
        self.assertEqual(Worker.seen, [])
        with self.assertRaises(Problem): self.manager.control(self.identity, 'resume')

    def test_unknown_expiry_remains_available(self):
        self.manager.update_account(self.identity, {'session_expires_at':None})
        self.assertIsNone(self.manager.get_account(self.identity)['session_expires_at'])
        self.assertEqual(self.request('POST', '/v1/chat/completions', self.payload())[0], 200)

    def test_session_update_preserves_quarantine_and_configuration(self):
        account = self.manager.get_account(self.identity)
        self.manager.quarantine(account, 'review required')
        new_session = SESSION.replace('A'*100, 'B'*100)
        result = self.manager.update_account(self.identity, {'sessionKey':new_session,'session_expires_at':int(time.time())+86400})
        self.assertTrue(result['restarted'])
        self.assertEqual(account['status'], 'quarantined')
        self.assertFalse(self.manager.inspect(account['container'])['running'])
        self.assertEqual(self.manager.account_details(self.identity)['sessionKey'], new_session)
        self.assertIsNone(account['verified_at'])
        config = Path(account['directory'])/'clewdr.toml'
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertIn('max_retries = 5', config.read_text())

    def test_config_update_rolls_back_after_failed_start(self):
        account = self.manager.get_account(self.identity)
        original = (Path(account['directory'])/'clewdr.toml').read_text()
        with patch.object(self.manager, 'wait_ready', side_effect=[Problem(502,'start failed'),'v0.13.5']):
            with self.assertRaises(Problem):
                self.manager.update_account(self.identity, {'sessionKey':SESSION.replace('A'*100, 'B'*100)})
        self.assertEqual(account['status'], 'ready')
        self.assertEqual((Path(account['directory'])/'clewdr.toml').read_text(), original)
        self.assertEqual(self.manager.account_details(self.identity)['sessionKey'], SESSION)

    def test_disabled_account_requires_manual_resume(self):
        self.manager.control(self.identity, 'disable')
        self.assertEqual(self.manager.snapshot()['accounts'][0]['status'], 'disabled')
        self.assertEqual(self.request('POST', '/v1/chat/completions', self.payload())[0], 503)
        self.manager.update_account(self.identity, {'email':'new@example.com'})
        self.assertEqual(self.manager.get_account(self.identity)['status'], 'disabled')

    def test_invalid_expiry_is_rejected_before_writes(self):
        original = self.manager.get_account(self.identity)['session_expires_at']
        with self.assertRaises(Problem): self.manager.update_account(self.identity, {'session_expires_at':'tomorrow'})
        self.assertEqual(self.manager.get_account(self.identity)['session_expires_at'], original)

    def test_update_timeout_does_not_restart_active_worker(self):
        account = self.manager.get_account(self.identity)
        with patch.object(self.manager, 'wait_idle', side_effect=Problem(409, 'request active')), patch.object(self.manager, 'run_worker') as run:
            with self.assertRaises(Problem):
                self.manager.update_account(self.identity, {'sessionKey':SESSION.replace('A'*100, 'B'*100)})
            run.assert_not_called()
        self.assertEqual(account['status'], 'ready')
        self.assertEqual(self.manager.account_details(self.identity)['sessionKey'], SESSION)

    def test_browser_metadata_is_editable_without_restart(self):
        result = self.manager.update_account(self.identity, {'user_agent':'Mozilla/5.0 (Windows NT 10.0) Chrome/148.0.0.0','os_label':'Win'})
        self.assertFalse(result['restarted'])
        details = self.manager.account_details(self.identity)
        self.assertEqual(details['os_label'], 'Win')
        self.assertEqual(details['user_agent'], self.manager.snapshot()['accounts'][0]['user_agent'])

    def test_default_expiry_is_import_time_plus_29_days(self):
        account = self.manager.get_account(self.identity)
        self.assertEqual(account['session_expires_at'], account['session_imported_at'] + 29*86400)
        self.assertEqual(account['session_expiry_source'], 'estimated')
        self.manager.update_account(self.identity, {'email':'owner@example.com','session_expires_at':account['session_expires_at']})
        self.assertEqual(account['session_expiry_source'], 'estimated')
        self.manager.update_account(self.identity, {'session_expires_at':int(time.time())+86400})
        self.assertEqual(account['session_expiry_source'], 'manual')

    def test_legacy_account_expiry_migration(self):
        account = self.manager.get_account(self.identity)
        account.pop('session_expiry_source');account['session_expires_at']=None
        self.manager.save()
        restored = FakeManager(self.temp.name, 'official:version', 'api-password', 'admin-password')
        current = restored.get_account(self.identity)
        self.assertEqual(current['session_expires_at'], current['created_at']+29*86400)
        self.assertEqual(current['session_expiry_source'], 'estimated')

    def test_explicit_unknown_expiry_survives_restart(self):
        self.manager.update_account(self.identity, {'session_expires_at':None})
        restored = FakeManager(self.temp.name, 'official:version', 'api-password', 'admin-password')
        self.assertIsNone(restored.get_account(self.identity)['session_expires_at'])

    def test_new_session_defaults_to_29_days_from_update(self):
        self.manager.update_account(self.identity, {'sessionKey':SESSION.replace('A'*100,'B'*100),'session_expires_at':None})
        account = self.manager.get_account(self.identity)
        self.assertEqual(account['session_expires_at'], account['session_imported_at']+29*86400)
        self.assertEqual(account['session_expiry_source'], 'estimated')

    def test_invalid_auth_and_unknown_routes_never_forward(self):
        self.assertEqual(self.request("POST", "/v1/chat/completions", self.payload(), key="wrong")[0], 401)
        self.assertEqual(self.request("POST", "/v1/responses", self.payload())[0], 404)
        self.assertEqual(Worker.seen, [])

    def test_gateway_replaces_credentials_and_strips_client_cookie(self):
        status, _, body = self.request("POST", "/v1/chat/completions", self.payload(), extra={"Cookie": "do-not-forward", "x-api-key": "api-password"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "OK")
        headers = Worker.seen[-1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer " + self.manager.get_account(self.identity)["key"])
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("x-api-key", headers)
        self.assert_idle()

    def test_sse_passes_through_and_releases_slot(self):
        status, headers, body = self.request("POST", "/v1/chat/completions", self.payload(True))
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["Content-Type"])
        self.assertIn(b"[DONE]", body)
        self.assertEqual(body.count(b"[DONE]"), 1)
        self.assert_idle()

    def test_multiple_500_failures_quarantine_and_survive_restart(self):
        Worker.status_code = 500
        for count in range(1, 4):
            status, _, body = self.request("POST", "/v1/chat/completions", self.payload())
            self.assertEqual(status, 500)
            self.assertNotIn(b"worker-secret", body)
            account = self.manager.get_account(self.identity)
            self.assertEqual(account["consecutive_failures"], count)
            self.assertEqual(account["status"], "quarantined" if count == 3 else "ready")
            self.assert_idle()
        restored = FakeManager(self.temp.name, "official:version", "api-password", "admin-password")
        self.assertEqual(restored.get_account(self.identity)["status"], "quarantined")
        self.assertEqual(self.request("POST", "/v1/chat/completions", self.payload())[0], 503)

    def second_account(self):
        return self.manager.add({"name": "second", "sessionKey": SESSION.replace("A" * 100, "B" * 100), "proxy": "127.0.0.1:1081"})

    def test_500_switches_account_and_preserves_request(self):
        second = self.second_account()
        first = self.manager.get_account(self.identity)
        Worker.replies["Bearer " + first["key"]] = (500, b'{"error":"secret"}')
        status, headers, body = self.request("POST", "/v1/chat/completions", self.payload())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "OK")
        self.assertEqual(len(Worker.seen), 2)
        self.assertEqual(Worker.seen[0]["body"], Worker.seen[1]["body"])
        self.assertNotEqual(Worker.seen[0]["headers"]["Authorization"], Worker.seen[1]["headers"]["Authorization"])
        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["consecutive_failures"], 1)
        self.assertIn("X-Request-Id", headers)
        self.assert_idle()

    def test_empty_response_switches_without_quarantine(self):
        self.second_account()
        first = self.manager.get_account(self.identity)
        Worker.replies["Bearer " + first["key"]] = (200, b'{"choices":[{"message":{"content":""}}]}')
        self.assertEqual(self.request("POST", "/v1/chat/completions", self.payload())[0], 200)
        self.assertEqual(first["status"], "ready")
        self.assertEqual(first.get("consecutive_failures", 0), 0)
        self.assertEqual(first["last_error"], "empty_response")
        self.assert_idle()

    def test_all_empty_returns_error_and_bounds_attempts(self):
        self.second_account()
        for account in self.manager.state["accounts"].values():
            Worker.replies["Bearer " + account["key"]] = (200, b'{"choices":[{"message":{"content":""}}]}')
        status, _, body = self.request("POST", "/v1/chat/completions", self.payload())
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"]["attempts"], 2)
        self.assertEqual(len(Worker.seen), 2)
        self.assertTrue(all(a["status"] == "ready" for a in self.manager.state["accounts"].values()))
        self.assert_idle()

    def test_rate_limit_cools_down_and_switches(self):
        self.second_account()
        first = self.manager.get_account(self.identity)
        Worker.replies["Bearer " + first["key"]] = (429, b'{"error":"rate"}')
        self.assertEqual(self.request("POST", "/v1/chat/completions", self.payload())[0], 200)
        self.assertEqual(first["status"], "ready")
        self.assertGreater(first["cooldown_until"], time.time())
        self.assertFalse(self.manager.available(first))
        chosen = self.manager.acquire()
        self.assertNotEqual(chosen["id"], first["id"])
        self.manager.release(chosen)
        self.assert_idle()

    def test_success_resets_failure_streak(self):
        Worker.status_code = 500
        self.request("POST", "/v1/chat/completions", self.payload())
        Worker.status_code = 200
        self.assertEqual(self.request("POST", "/v1/chat/completions", self.payload())[0], 200)
        self.assertEqual(self.manager.get_account(self.identity)["consecutive_failures"], 0)

    def test_stream_error_before_output_switches(self):
        self.second_account()
        first = self.manager.get_account(self.identity)
        Worker.replies["Bearer " + first["key"]] = (200, b'data: {"type":"error","error":{"type":"overloaded_error"}}\n\n', "text/event-stream")
        status, _, body = self.request("POST", "/v1/chat/completions", self.payload(True))
        self.assertEqual(status, 200)
        self.assertIn(b"OK", body)
        self.assertNotIn(b"overloaded_error", body)
        self.assertEqual(first["consecutive_failures"], 1)
        self.assert_idle()

    def test_probe_500_uses_failure_threshold(self):
        Worker.status_code = 500
        with self.assertRaises(Problem):
            self.manager.probe(self.identity)
        self.assertEqual(self.manager.get_account(self.identity)["status"], "ready")
        self.assertEqual(self.manager.get_account(self.identity)["consecutive_failures"], 1)

    def test_attempt_limit_does_not_touch_fourth_account(self):
        for letter in "BCD":
            self.manager.add({"name": letter, "sessionKey": SESSION.replace("A" * 100, letter * 100), "proxy": "127.0.0.1:1080"})
        Worker.status_code = 500
        status, _, body = self.request("POST", "/v1/chat/completions", self.payload())
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body)["error"]["attempts"], 3)
        self.assertEqual(len(Worker.seen), 3)
        self.assertEqual(sum(a.get("consecutive_failures", 0) for a in self.manager.state["accounts"].values()), 3)
        self.assert_idle()

    def test_stream_failure_after_content_is_not_replayed(self):
        self.second_account()
        first = self.manager.get_account(self.identity)
        data = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\ndata: {"type":"error","error":{"type":"overloaded_error"}}\n\n'
        # Split at the first valid event to simulate a failure after output starts.
        class PartialWorker(Worker):
            def do_POST(worker):
                body = worker.rfile.read(int(worker.headers["Content-Length"]))
                Worker.seen.append({"body": body})
                prefix, tail = data.split(b'data: {"type":"error"', 1)
                tail = b'data: {"type":"error"' + tail
                worker.send_response(200)
                worker.send_header("Content-Type", "text/event-stream")
                worker.send_header("Content-Length", str(len(data)))
                worker.end_headers()
                worker.wfile.write(prefix)
                worker.wfile.flush()
                time.sleep(.08)
                worker.wfile.write(tail)
        worker = ThreadingHTTPServer(("127.0.0.1", 0), PartialWorker)
        threading.Thread(target=worker.serve_forever, daemon=True).start()
        first["port"] = worker.server_port
        try:
            with self.assertRaises(http.client.IncompleteRead):
                self.request("POST", "/v1/chat/completions", self.payload(True))
            self.assertEqual(len(Worker.seen), 1)
            self.assertEqual(first["consecutive_failures"], 1)
            self.assertEqual(first["status"], "ready")
            self.assert_idle()
        finally:
            worker.shutdown()
            worker.server_close()

    def test_connection_failure_switches_without_immediate_quarantine(self):
        self.second_account()
        first = self.manager.get_account(self.identity)
        with socket.socket() as blocked:
            blocked.bind(("127.0.0.1", 0))
            first["port"] = blocked.getsockname()[1]
        self.manager.worker_seconds = .1
        self.assertEqual(self.request("POST", "/v1/chat/completions", self.payload())[0], 200)
        self.assertEqual(first["consecutive_failures"], 1)
        self.assertEqual(first["status"], "ready")
        self.assert_idle()

    def test_worker_401_isolated_and_other_account_used(self):
        self.second_account()
        first = self.manager.get_account(self.identity)
        Worker.replies["Bearer " + first["key"]] = (401, b'{"error":"credentials"}')
        self.assertEqual(self.request("POST", "/v1/chat/completions", self.payload())[0], 200)
        self.assertEqual(first["status"], "quarantined")
        self.assert_idle()

    def test_client_input_error_does_not_quarantine(self):
        Worker.status_code = 400
        self.assertEqual(self.request("POST", "/v1/chat/completions", self.payload())[0], 400)
        self.assertEqual(self.manager.get_account(self.identity)["status"], "ready")

    def test_duplicate_import_does_not_reactivate(self):
        self.manager.quarantine(self.manager.get_account(self.identity), "test")
        with self.assertRaises(Problem) as error:
            self.manager.add({"sessionKey": SESSION, "proxy": "host:1080"})
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.manager.get_account(self.identity)["status"], "quarantined")

    def test_account_concurrency_is_one_and_queue_has_timeout(self):
        account = self.manager.acquire()
        result = []
        def wait():
            try:
                self.manager.acquire()
            except Problem as error:
                result.append(error.status)
        thread = threading.Thread(target=wait)
        thread.start()
        thread.join(2)
        self.assertEqual(result, [429])
        self.assertEqual(self.manager.total, 1)
        self.manager.release(account)
        self.assertEqual(self.manager.total, 0)

    def test_manual_pause_and_restore(self):
        self.manager.control(self.identity, "pause")
        self.assertEqual(self.manager.get_account(self.identity)["status"], "paused")
        self.assertFalse(self.manager.inspect("clewdr-" + self.identity)["running"])
        self.manager.control(self.identity, "resume")
        self.assertEqual(self.manager.get_account(self.identity)["status"], "ready")

    def test_probe_runs_real_protocol_and_records_success(self):
        result = self.manager.probe(self.identity)
        self.assertEqual(result["content"], "OK")
        self.assertIsNotNone(self.manager.get_account(self.identity)["verified_at"])

    def test_failed_candidate_keeps_old_image_and_status(self):
        sha = "b" * 40
        def github(path):
            if path == "/commits/master":
                return {"sha": sha}
            return {"workflow_runs": [{"name": "build", "head_sha": sha, "conclusion": "success"}]}
        with patch.object(updater, "github", side_effect=github), patch.object(updater, "validate", side_effect=Problem(502, "test rejected")):
            result = self.manager.check_upstream("master", True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.manager.get_account(self.identity)["image"], "official:version")
        self.assertEqual(self.manager.get_account(self.identity)["status"], "ready")
        self.assertEqual(self.manager.total, 0)
        self.assertFalse(any("check-" in name for name in self.manager.containers))

    def test_failed_official_build_is_not_deployed(self):
        with patch.object(updater, "github", side_effect=[{"sha": "c" * 40}, {"workflow_runs": []}]):
            result = self.manager.check_upstream("master", True)
        self.assertEqual(result["status"], "waiting_build")
        self.assertEqual(self.manager.get_account(self.identity)["image"], "official:version")

    def test_stream_state_recognizes_split_completion_and_error(self):
        stream = StreamState()
        stream.feed(b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DO')
        self.assertFalse(stream.finished)
        self.assertTrue(stream.openai_chunk)
        stream.feed(b'NE]\n\n')
        self.assertTrue(stream.finished)
        self.assertFalse(stream.failed)
        native = StreamState()
        native.feed(b'data: {"type":"error","error":{"message":"failure"}}\n\ndata: {"type":"message_stop"}\n\n')
        self.assertTrue(native.failed)
        self.assertTrue(native.finished)

    def test_successful_candidate_promotes_pinned_image(self):
        sha = "d" * 40
        with patch.object(updater, "github", side_effect=[{"sha": sha}, {"workflow_runs": [{"name": "build", "head_sha": sha, "conclusion": "success"}]}]):
            result = self.manager.check_upstream("master", True)
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["deployed_sha"], sha)
        self.assertIn("@sha256:", self.manager.get_account(self.identity)["image"])
        self.assertEqual(self.manager.get_account(self.identity)["status"], "ready")
        self.assertEqual(self.manager.total, 0)
        restored = FakeManager(self.temp.name, "official:version", "api-password", "admin-password")
        self.assertEqual(restored.image, self.manager.get_account(self.identity)["image"])

    def add_resources(self, count):
        return [self.identity] + [self.manager.add({"name": "resource-" + str(i),
                "sessionKey": "sk-ant-sid02-" + chr(66 + i) * 100 + "-ABCDEFAA",
                "proxy": "127.0.0.1:1080"}) for i in range(count - 1)]

    def test_lru_balances_sequential_requests(self):
        ids = self.add_resources(3)
        selected = []
        for _ in range(12):
            account = self.manager.acquire()
            selected.append(account["id"])
            self.manager.release(account)
        self.assertEqual(selected, ids * 4)

    def test_simultaneous_requests_reserve_distinct_resources(self):
        ids = self.add_resources(4)
        start, acquired = threading.Barrier(4), threading.Barrier(4)
        selected, failures = [], []
        def request():
            try:
                start.wait(timeout=2)
                account = self.manager.acquire()
                selected.append(account["id"])
                acquired.wait(timeout=2)
                self.manager.release(account)
            except Exception as error:
                failures.append(error)
        threads = [threading.Thread(target=request) for _ in ids]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=3)
        self.assertEqual(failures, [])
        self.assertEqual(set(selected), set(ids))
        self.assertEqual(len(selected), len(set(selected)))
        self.assert_idle()

    def test_busy_and_quarantined_resources_are_not_selected(self):
        ids = self.add_resources(3)
        held = self.manager.acquire(ids[0])
        self.manager.quarantine(self.manager.get_account(ids[1]), "test")
        for _ in range(3):
            account = self.manager.acquire()
            self.assertEqual(account["id"], ids[2])
            self.manager.release(account)
        self.manager.release(held)


if __name__ == "__main__":
    unittest.main()
