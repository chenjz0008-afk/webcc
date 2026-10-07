import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import tomllib
import uuid
from api_keys import ApiKeys, KeyProblem
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
SESSION_ESTIMATE_SECONDS = 29 * 86400
SESSION = re.compile(r"sk-ant-sid\d{2}-[A-Za-z0-9_-]{86,120}-[A-Za-z0-9_-]{6}AA")
POST_ROUTES = {"/v1/messages", "/v1/chat/completions", "/code/v1/messages",
               "/code/v1/chat/completions", "/code/v1/messages/count_tokens"}
GET_ROUTES = {"/v1/models", "/code/v1/models"}


class Problem(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class ClientGone(Exception):
    pass


class StreamState:
    def __init__(self):
        self.buffer, self.finished, self.failed = b"", False, False
        self.openai_chunk, self.meaningful, self.error_status = False, False, 502

    def feed(self, chunk):
        self.buffer += chunk
        lines = self.buffer.split(b"\n")
        self.buffer = lines.pop()[-65536:]
        for line in lines:
            if not line.startswith(b"data:"):
                continue
            value = line[5:].strip()
            if value == b"[DONE]":
                self.finished = True
                continue
            try:
                event = json.loads(value)
                if isinstance(event, dict):
                    choices = event.get("choices")
                    if isinstance(choices, list):
                        self.openai_chunk |= any(isinstance(choice, dict) and isinstance(choice.get("delta"), dict)
                                                 for choice in choices)
                    if isinstance(choices, list):
                        self.meaningful |= any(isinstance(c, dict) and isinstance(c.get("delta"), dict) and
                                               any(c["delta"].get(k) for k in ("content", "reasoning_content", "tool_calls", "refusal")) for c in choices)
                    delta = event.get("delta", {})
                    if isinstance(delta, dict):
                        self.meaningful |= any(delta.get(k) for k in ("text", "thinking", "partial_json"))
                    block = event.get("content_block", {})
                    if isinstance(block, dict):
                        self.meaningful |= bool(block.get("text") or block.get("thinking") or block.get("type") == "tool_use")
                    error = event.get("error", {})
                    if isinstance(error, dict):
                        self.error_status = {"authentication_error": 401, "permission_error": 403,
                                             "rate_limit_error": 429, "overloaded_error": 503,
                                             "invalid_request_error": 400}.get(error.get("type"), 502)
                    self.finished |= event.get("type") == "message_stop"
                    self.failed |= event.get("type") == "error" or bool(event.get("error"))
            except ValueError:
                pass


def normalize_proxy(value, username="", password=""):
    value = value.strip()
    if "://" not in value:
        parts = value.split(":", 3)
        if len(parts) == 4:
            value = "socks5://{}:{}@{}:{}".format(quote(parts[2], safe=""),
                    quote(parts[3], safe=""), parts[0], parts[1])
        else:
            value = "socks5://" + value
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https", "socks5", "socks5h"}:
            raise ValueError()
        if not parsed.hostname or not parsed.port or parsed.path not in {"", "/"}:
            raise ValueError()
        if parsed.query or parsed.fragment or any(c in value for c in "\r\n\t "):
            raise ValueError()
        host = parsed.hostname
        if re.fullmatch(r"[0-9.]+", host) or ":" in host:
            try:
                ipaddress.ip_address(host)
            except ValueError:
                raise Problem(400, "代理 IP 格式无效，IPv4 必须完整填写四段，例如 140.228.28.66")
        host = "[" + host + "]" if ":" in host else host
        if username or password:
            if parsed.username is not None:
                raise ValueError()
            auth = quote(username, safe="") + ":" + quote(password, safe="") + "@"
        elif parsed.username is not None:
            auth = quote(unquote(parsed.username), safe="") + ":" + quote(unquote(parsed.password or ""), safe="") + "@"
        else:
            auth = ""
        return parsed.scheme + "://" + auth + host + ":" + str(parsed.port)
    except ValueError:
        raise Problem(400, "代理必须包含地址和端口，例如 IP:端口，或 socks5://用户:密码@IP:端口")


def proxy_label(value):
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    host = "[" + host + "]" if ":" in host else host
    return parsed.scheme + "://" + host + ":" + str(parsed.port)


def account_profile(fields, current=None):
    values = {k: (current or {}).get(k, "") for k in ("email", "email_password", "notes")}
    for key, limit in (("email", 254), ("email_password", 1024), ("notes", 2000)):
        if key in fields:
            if not isinstance(fields[key], str) or len(fields[key]) > limit:
                raise Problem(400, "账号资料格式或长度无效")
            values[key] = fields[key].strip() if key != "email_password" else fields[key]
    expires = fields.get("session_expires_at", (current or {}).get("session_expires_at"))
    if expires not in (None, ""):
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not 0 < expires < 32503680000:
            raise Problem(400, "sessionKey 到期时间无效")
        expires = int(expires)
    values["session_expires_at"] = expires or None
    previous = current or {}
    values["session_expiry_source"] = previous.get("session_expiry_source", "manual" if expires else "unknown")
    if "session_expires_at" in fields:
        if not expires:
            values["session_expiry_source"] = "unknown"
        elif expires != previous.get("session_expires_at"):
            values["session_expiry_source"] = "manual"
    return values


def probe_proxy(proxy):
    started = time.monotonic()
    try:
        result = subprocess.run(["curl", "--disable", "--config", "-", "--silent", "--fail",
                                 "--connect-timeout", "8", "--max-time", "20", "--max-filesize", "4096",
                                 "--noproxy", "", "https://api.ipify.org?format=json"],
                                input="proxy = " + json.dumps(proxy) + "\n", capture_output=True, text=True, timeout=25)
        if result.returncode:
            messages = {5: "代理地址无法解析", 7: "无法连接代理", 28: "代理连接超时", 60: "HTTPS 证书验证失败", 97: "代理握手或认证失败"}
            raise Problem(400, messages.get(result.returncode, "代理 HTTPS 出口检测失败，请检查地址、认证和协议"))
        ip = str(ipaddress.ip_address(json.loads(result.stdout)["ip"]))
        return {"ok": True, "ip": ip, "latency_ms": round((time.monotonic() - started) * 1000), "checked_at": int(time.time())}
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        raise Problem(400, "代理检测失败或返回无效出口 IP，请检查代理后重试")


def parse_account_text(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise Problem(400, "文件为空")
    header = lines[:lines.index("[")] if "[" in lines else lines[:20]
    proxy = next((line for line in header if re.match(r"^(?:\w+://|\d+\.\d+\.\d+\.\d+:\d+)", line)), "")
    ua = next((line for line in header if line.startswith("Mozilla/")), "")
    label = next((line for line in header if re.match(r"^(?:macOS|Windows|Linux|Android)\S*", line, re.I)), "")
    return {"sessionKey": lines[0], "proxy": proxy, "user_agent": ua, "os_label": label}


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(temp, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class Manager:
    def __init__(self, data, image, api_key, admin_key, max_inflight=4, queue_size=16, queue_seconds=15):
        if not api_key or not admin_key or api_key == admin_key:
            raise ValueError("必须设置两个不同的 API 和管理员密码")
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data.chmod(0o700)
        self.path = self.data / "registry.json"
        self.image, self.api_key, self.admin_key = image, api_key, admin_key
        self.max_inflight, self.queue_size, self.queue_seconds = max_inflight, queue_size, queue_seconds
        self.web_tools_enabled = os.environ.get("MANAGER_WEB_TOOLS_ENABLED", "false").lower() == "true"
        if self.web_tools_enabled:
            import web_tools.api
        self.retry_attempts = max(1, min(5, int(os.environ.get("MANAGER_RETRY_ATTEMPTS", "3"))))
        self.failure_threshold = max(2, int(os.environ.get("MANAGER_FAILURE_THRESHOLD", "3")))
        self.request_seconds = max(10, float(os.environ.get("MANAGER_REQUEST_SECONDS", "150")))
        self.worker_seconds = max(5, float(os.environ.get("MANAGER_WORKER_SECONDS", "60")))
        self.condition = threading.Condition(threading.RLock())
        self.operations = threading.RLock()
        self.proxy_checks = threading.BoundedSemaphore(2)
        self.inflight, self.total, self.waiting, self.cursor = {}, 0, 0, 0
        self.last_used, self.dispatches = {}, {}
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"accounts": {}, "update": {}}
        self.api_keys = ApiKeys(self)
        self.image = self.state["update"].get("image", image)
        for account in self.state["accounts"].values():
            account.setdefault("session_imported_at", account.get("created_at", int(time.time())))
            if "session_expiry_source" not in account:
                if not account.get("session_expires_at"):
                    account["session_expires_at"] = account["session_imported_at"] + SESSION_ESTIMATE_SECONDS
                    account["session_expiry_source"] = "estimated"
                else:
                    account["session_expiry_source"] = "manual"
            if account["status"] in {"pending", "updating"}:
                account.update(status="paused", reason="上次管理操作未完成，需人工启动")
        self.save()

    def save(self):
        with self.condition:
            private_write(self.path, json.dumps(self.state, ensure_ascii=False, indent=2))
            self.condition.notify_all()

    @contextmanager
    def operation(self):
        if not self.operations.acquire(blocking=False):
            raise Problem(409, "其他管理操作正在进行，请稍后再试")
        try:
            yield
        finally:
            self.operations.release()

    def docker(self, *args, timeout=60):
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise Problem(502, "Docker 操作失败：" + str(args[0]))
        return result.stdout.strip()

    def inspect(self, name):
        try:
            value = json.loads(self.docker("inspect", name))[0]
            return {"running": value["State"]["Running"], "status": value["State"]["Status"],
                    "mounts": [{"source": v["Source"], "destination": v["Destination"], "writable": v["RW"]}
                               for v in value["Mounts"]], "image_id": value["Image"]}
        except (Problem, KeyError, ValueError):
            return {"running": False, "status": "missing", "mounts": []}

    def snapshot(self):
        with self.condition:
            accounts = [dict(a) for a in self.state["accounts"].values()]
            update = dict(self.state["update"])
            counts = dict(self.inflight)
            total, waiting = self.total, self.waiting
        output = []
        for account in accounts:
            public = {k: v for k, v in account.items() if k not in {"key", "session_hash", "email_password"}}
            if account["status"] == "ready" and self.expired(account):
                public.update(status="expired", reason="sessionKey 已到期，请更换并更新到期时间")
            inspection = self.inspect(account["container"])
            public.update(running=inspection["running"], docker_status=inspection["status"],
                          mounts=inspection["mounts"], image_id=inspection.get("image_id"))
            public["inflight"] = counts.get(account["id"], 0)
            public["dispatches"] = self.dispatches.get(account["id"], 0)
            output.append(public)
        return {"accounts": output, "update": update, "inflight": total, "waiting": waiting,
                "limits": {"per_account": 1, "total": self.max_inflight, "queue": self.queue_size,
                           "queue_seconds": self.queue_seconds, "retry_attempts": self.retry_attempts,
                           "failure_threshold": self.failure_threshold, "request_seconds": self.request_seconds,
                           "worker_seconds": self.worker_seconds},
                "settings": {"update_seconds": int(os.environ.get("MANAGER_UPDATE_SECONDS", "86400")),
                             "follow": os.environ.get("MANAGER_FOLLOW", "master"),
                             "auto_update": os.environ.get("MANAGER_AUTO_UPDATE", "true").lower() == "true"}}

    def set_status(self, account, status, reason=""):
        with self.condition:
            account.update(status=status, reason=reason)
            self.save()

    def free_port(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    def run_worker(self, account, image=None, directory=None, port=None, name=None):
        network = self.worker_network(account, directory)
        self.docker("run", "-d", "--name", name or account["container"], "--restart", "unless-stopped",
                    *network,
                    "--label", "clewdr-manager=true", "--label", "account-id=" + account["id"],
                    "--publish", "127.0.0.1:{}:8484".format(port or account["port"]),
                    "--mount", "type=bind,src={},dst=/etc/clewdr".format(directory or account["directory"]),
                    "--log-driver", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,noexec,size=16m",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--user", "0:0",
                    "--memory", "256m", "--pids-limit", "64", "--cpus", "1", image or account["image"])

    def worker_network(self, account, directory=None):
        if os.environ.get("MANAGER_PROXY_ONLY", "false").lower() != "true":
            return []
        import egress
        path = Path(directory or account["directory"]) / "clewdr.toml"
        text = path.read_text()
        config = tomllib.loads(text)
        try:
            proxy = config["proxy"]
            if proxy.startswith("socks5://"):
                proxy = "socks5h://" + proxy[len("socks5://"):]
            network = egress.prepare(self.data, account["id"], proxy)
            if proxy != config["proxy"]:
                private_write(path, re.sub(r"(?m)^proxy\s*=.*$", lambda _: "proxy = " + json.dumps(proxy), text))
            return network
        except egress.EgressError:
            raise Problem(503, "出口白名单未能就绪，拒绝启动账号容器") from None

    def wait_ready(self, port):
        for _ in range(40):
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                connection.request("GET", "/api/version")
                response = connection.getresponse()
                version = response.read(4096).decode(errors="replace").splitlines()[0]
                connection.close()
                if response.status == 200:
                    return version
            except (OSError, http.client.HTTPException, IndexError):
                pass
            time.sleep(0.25)
        raise Problem(502, "容器未能就绪")

    def add(self, fields):
        with self.operation():
            if "text" in fields:
                fields = {**parse_account_text(fields["text"]), **{k: v for k, v in fields.items() if k != "text"}}
            session = fields.get("sessionKey", "").strip()
            if session.startswith("sessionKey="):
                session = session[len("sessionKey="):]
            if not SESSION.fullmatch(session):
                raise Problem(400, "sessionKey 格式不符合当前 ClewdR 版本")
            profile = account_profile(fields)
            imported_at = int(time.time())
            if not profile["session_expires_at"]:
                profile.update(session_expires_at=imported_at + SESSION_ESTIMATE_SECONDS, session_expiry_source="estimated")
            proxy = normalize_proxy(fields.get("proxy", ""), fields.get("proxy_username", ""), fields.get("proxy_password", ""))
            digest = hashlib.sha256(session.encode()).hexdigest()
            if any(a["session_hash"] == digest for a in self.state["accounts"].values()):
                raise Problem(409, "该 sessionKey 已存在；重复导入不会恢复被隔离的账号")
            proxy_check = self.test_proxy(fields)
            identity = secrets.token_hex(6)
            directory = self.data / "accounts" / identity
            directory.mkdir(parents=True, mode=0o700)
            directory.chmod(0o700)
            key = secrets.token_urlsafe(36)
            settings = {"ip": "0.0.0.0", "port": 8484, "password": key, "admin_password": secrets.token_urlsafe(36),
                        "proxy": proxy, "check_update": False, "auto_update": False,
                        "max_retries": 5, "skip_restricted": True, "skip_first_warning": False,
                        "skip_second_warning": False, "no_fs": False, "log_to_file": False,
                        "preserve_chats": False, "web_search": False, "enable_web_count_tokens": False,
                        "sanitize_messages": False, "skip_non_pro": False, "skip_normal_pro": False,
                        "skip_rate_limit": True, "use_real_roles": True}
            config = "\n".join(k + " = " + json.dumps(v, ensure_ascii=False) for k, v in settings.items())
            config += "\n\n[[cookie_array]]\ncookie = " + json.dumps(session) + "\n"
            private_write(directory / "clewdr.toml", config)
            account = {"id": identity, "name": str(fields.get("name", "") or "账号-" + identity)[:80],
                       "container": "clewdr-" + identity, "port": self.free_port(), "directory": str(directory),
                       "key": key, "session_hash": digest, "proxy": proxy_label(proxy),
                       "user_agent": str(fields.get("user_agent", ""))[:512], "os_label": str(fields.get("os_label", ""))[:80],
                       "fingerprint": "原版 Chrome145；UA/系统标签仅保存，未应用", "image": self.image,
                       "status": "pending", "reason": "", "created_at": int(time.time()), "verified_at": None,
                       "proxy_check": proxy_check, "session_imported_at": imported_at, **profile}
            with self.condition:
                self.state["accounts"][identity] = account
                self.save()
            try:
                self.run_worker(account)
                account["version"] = self.wait_ready(account["port"])
                self.set_status(account, "ready")
            except Exception:
                self.set_status(account, "error", "容器创建失败，可检查 Docker 后重新启动")
                raise
            return identity

    @staticmethod
    def expired(account):
        return bool(account.get("session_expires_at") and account["session_expires_at"] <= time.time())

    def available(self, account):
        return account["status"] == "ready" and not self.expired(account) and account.get("cooldown_until", 0) <= time.time()

    def account_details(self, identity):
        with self.operation():
            account = self.get_account(identity)
            config = tomllib.loads((Path(account["directory"]) / "clewdr.toml").read_text())
            proxy = urlsplit(config["proxy"])
            return {**account_profile({}, account), "name": account["name"],
                    "user_agent": account.get("user_agent", ""), "os_label": account.get("os_label", ""),
                    "proxy": proxy_label(config["proxy"]), "proxy_username": unquote(proxy.username or ""),
                    "proxy_password": unquote(proxy.password or ""),
                    "sessionKey": config["cookie_array"][0]["cookie"]}

    def update_account(self, identity, fields):
        with self.operation():
            account = self.get_account(identity)
            profile = account_profile(fields, account)
            name = fields.get("name", account["name"])
            if not isinstance(name, str) or not name.strip() or len(name) > 80:
                raise Problem(400, "请填写不超过 80 字的账号名称")
            config_path = Path(account["directory"]) / "clewdr.toml"
            old_text = config_path.read_text()
            config = tomllib.loads(old_text)
            proxy = normalize_proxy(fields["proxy"], fields.get("proxy_username", ""), fields.get("proxy_password", "")) if "proxy" in fields else config["proxy"]
            session = fields.get("sessionKey", "").strip() or config["cookie_array"][0]["cookie"]
            if not SESSION.fullmatch(session):
                raise Problem(400, "sessionKey 格式不符合当前 ClewdR 版本")
            digest = hashlib.sha256(session.encode()).hexdigest()
            if any(a["id"] != identity and a["session_hash"] == digest for a in self.state["accounts"].values()):
                raise Problem(409, "该 sessionKey 已被其他账号使用")
            changed = proxy != config["proxy"] or digest != account["session_hash"]
            updated_at = int(time.time())
            if digest != account["session_hash"] and not fields.get("session_expires_at"):
                profile.update(session_expires_at=updated_at + SESSION_ESTIMATE_SECONDS, session_expiry_source="estimated")
            elif digest != account["session_hash"]:
                profile["session_expiry_source"] = "manual"
            changes = {**profile, "name": name.strip(), "updated_at": updated_at}
            if digest != account["session_hash"]:
                changes["session_imported_at"] = updated_at
            for field, limit in (("user_agent", 512), ("os_label", 80)):
                if field in fields:
                    if not isinstance(fields[field], str) or len(fields[field]) > limit:
                        raise Problem(400, "浏览器资料格式或长度无效")
                    changes[field] = fields[field].strip()
            if not changed:
                with self.condition:
                    account.update(changes)
                    self.save()
                return {"ok": True, "restarted": False}
            proxy_check = self.test_proxy({"proxy": proxy})
            original = dict(account)
            self.set_status(account, "updating", "正在更新账号配置")
            try:
                self.wait_idle(account)
            except Exception:
                self.set_status(account, original["status"], original["reason"])
                raise
            try:
                private_write(self.data / "backups" / (identity + "-profile-" + str(time.time_ns()) + ".toml"), old_text)
                new_text = re.sub(r"(?m)^proxy\s*=.*$", lambda _: "proxy = " + json.dumps(proxy), old_text)
                new_text = re.sub(r"(?m)^cookie\s*=.*$", lambda _: "cookie = " + json.dumps(session), new_text)
                private_write(config_path, new_text)
                self.docker("stop", account["container"])
                self.docker("rm", account["container"])
                self.run_worker(account)
                version = self.wait_ready(account["port"])
                if original["status"] != "ready":
                    self.docker("stop", account["container"])
                with self.condition:
                    account.update(changes, proxy=proxy_label(proxy), session_hash=digest, proxy_check=proxy_check,
                                   version=version, verified_at=None, status=original["status"], reason=original["reason"])
                    self.save()
            except Exception:
                try:
                    private_write(config_path, old_text)
                    if self.inspect(account["container"])["status"] != "missing":
                        self.docker("stop", account["container"])
                        self.docker("rm", account["container"])
                    self.run_worker(original)
                    self.wait_ready(original["port"])
                    if original["status"] != "ready":
                        self.docker("stop", original["container"])
                    with self.condition:
                        account.update(original)
                        self.save()
                except Exception:
                    self.set_status(account, "error", "配置更新和回退失败，请检查容器")
                raise
            return {"ok": True, "restarted": True}

    def test_proxy(self, fields):
        proxy = normalize_proxy(fields.get("proxy", ""), fields.get("proxy_username", ""), fields.get("proxy_password", ""))
        if not self.proxy_checks.acquire(blocking=False):
            raise Problem(429, "代理检测正在进行，请稍后重试")
        try:
            return probe_proxy(proxy)
        finally:
            self.proxy_checks.release()

    def check_account_proxy(self, identity):
        account = self.get_account(identity)
        with self.operation():
            config = tomllib.loads((Path(account["directory"]) / "clewdr.toml").read_text())
            try:
                result = self.test_proxy({"proxy": config["proxy"]})
            except Problem as error:
                result = {"ok": False, "message": str(error), "checked_at": int(time.time())}
                with self.condition:
                    account["proxy_check"] = result
                    self.save()
                raise
            with self.condition:
                account["proxy_check"] = result
                self.save()
            return result

    def get_account(self, identity):
        account = self.state["accounts"].get(identity)
        if account is None:
            raise Problem(404, "账号不存在")
        return account

    def wait_idle(self, account, seconds=60):
        deadline = time.monotonic() + seconds
        with self.condition:
            while self.inflight.get(account["id"], 0):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Problem(409, "仍有进行中的请求，请稍后重试")
                self.condition.wait(remaining)

    def control(self, identity, action):
        with self.operation():
            account = self.get_account(identity)
            if action in {"pause", "disable"}:
                self.set_status(account, "disabled" if action == "disable" else "paused", "管理员标记不可用" if action == "disable" else "管理员暂停")
                self.wait_idle(account)
                self.docker("stop", account["container"])
            elif action == "resume":
                if self.expired(account):
                    raise Problem(400, "sessionKey 已到期，请先更新凭证和到期时间")
                if account["status"] == "ready":
                    return
                self.wait_idle(account)
                if account["image"] != self.image:
                    from updater import replace_worker
                    replace_worker(self, account, self.image)
                elif self.inspect(account["container"])["status"] == "missing":
                    self.run_worker(account)
                else:
                    network = self.worker_network(account)
                    if network:
                        existing = json.loads(self.docker("inspect", account["container"]))[0]
                        if existing["HostConfig"]["NetworkMode"] != network[1]:
                            raise Problem(503, "旧容器尚未迁移至代理专用网络，拒绝启动")
                    self.docker("start", account["container"])
                account["version"] = self.wait_ready(account["port"])
                account.update(consecutive_failures=0, cooldown_until=0)
                self.set_status(account, "ready", "管理员显式恢复")
            else:
                raise Problem(404, "操作不存在")

    def acquire(self, identity=None, statuses=("ready",), exclude=(), request_deadline=None, on_wait=None, allowed=None):
        deadline = min(time.monotonic() + self.queue_seconds, request_deadline or float("inf"))
        with self.condition:
            if self.waiting >= self.queue_size:
                raise Problem(429, "等待队列已满")
            self.waiting += 1
            try:
                while True:
                    if on_wait:
                        on_wait()
                    eligible = [a for a in self.state["accounts"].values() if a["status"] in statuses and a["id"] not in exclude and a.get("cooldown_until", 0) <= time.time() and not self.expired(a) and (identity is None or a["id"] == identity) and (allowed is None or a["id"] in allowed)]
                    if not eligible:
                        raise Problem(503, "没有可用账号，请查看管理页面")
                    available = [a for a in eligible if not self.inflight.get(a["id"], 0)]
                    if available and self.total < self.max_inflight:
                        account = min(available, key=lambda item: self.last_used.get(item["id"], 0))
                        self.cursor += 1
                        self.last_used[account["id"]] = self.cursor
                        self.dispatches[account["id"]] = self.dispatches.get(account["id"], 0) + 1
                        self.inflight[account["id"]] = 1
                        self.total += 1
                        return account
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Problem(429, "等待可用账号超时")
                    self.condition.wait(min(remaining, .2) if on_wait else remaining)
            finally:
                self.waiting -= 1

    def release(self, account):
        with self.condition:
            self.inflight.pop(account["id"], None)
            self.total -= 1
            self.condition.notify_all()

    def quarantine(self, account, reason):
        self.set_status(account, "quarantined", reason)

    def record_failure(self, account, kind, status=None, retry_after=None):
        with self.condition:
            account.update(last_error=kind, last_error_at=int(time.time()))
            if status in {401, 403}:
                account.update(status="quarantined", reason="上游认证或访问拒绝，HTTP " + str(status))
            elif status == 429:
                try:
                    delay = min(3600, max(30, int(retry_after or 60)))
                except (ValueError, TypeError):
                    delay = 60
                account["cooldown_until"] = time.time() + delay
            elif kind != "empty_response" and (status is None or status >= 500):
                failures = account.get("consecutive_failures", 0) + 1
                account["consecutive_failures"] = failures
                if failures >= self.failure_threshold:
                    account.update(status="quarantined", reason=f"连续 {failures} 次通道失败；最近错误：{kind}")
            self.save()

    def record_success(self, account):
        with self.condition:
            account.update(verified_at=int(time.time()), consecutive_failures=0, cooldown_until=0)
            self.save()

    def probe(self, identity):
        account = self.acquire(identity)
        connection = http.client.HTTPConnection("127.0.0.1", account["port"], timeout=120)
        try:
            body = json.dumps({"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 16, "stream": False})
            connection.request("POST", "/v1/chat/completions", body, {"Authorization": "Bearer " + account["key"], "Content-Type": "application/json", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            data = response.read(1024 * 1024)
            if response.status != 200:
                self.record_failure(account, "HTTP " + str(response.status), response.status, response.getheader("Retry-After"))
                raise Problem(502, "生成验证失败，HTTP " + str(response.status))
            payload = json.loads(data)
            content = payload["choices"][0]["message"]["content"]
            if not content:
                self.record_failure(account, "empty_response")
                raise Problem(502, "生成验证返回空正文")
            self.record_success(account)
            return {"ok": True, "content": content, "model": payload.get("model")}
        except (OSError, http.client.HTTPException):
            self.record_failure(account, "connection_or_stream_error")
            raise Problem(502, "生成验证连接异常")
        except (ValueError, KeyError, IndexError):
            self.record_failure(account, "empty_response")
            raise Problem(502, "生成验证响应格式异常")
        finally:
            connection.close()
            self.release(account)

    def check_upstream(self, follow="master", auto=True):
        from updater import check
        return check(self, follow, auto)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ClewdR-Manager"

    def log_message(self, *args):
        pass

    @property
    def manager(self):
        return self.server.manager

    def respond(self, status, data, content_type="application/json; charset=utf-8"):
        if status >= 400 and isinstance(data, dict):
            from protocol_errors import applies, wrap
            if applies(self.path):
                data = wrap(status, data, self.request_id)
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        try:
            self.send_response(status)
            if self.path.startswith("/admin/"):
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", content_type)
            if getattr(self, "adapter", None):
                self.send_header("X-WebCC-Adapter", self.adapter)
            if getattr(self, "retry_after", None) is not None:
                self.send_header("Retry-After", str(self.retry_after))
            if getattr(self, "request_id", None):
                self.send_header("X-Request-Id", self.request_id)
                self.send_header("Request-Id", self.request_id)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            raise ClientGone()

    def authenticate(self, admin=False):
        candidate = self.headers.get("Authorization", "")
        candidate = candidate[7:] if candidate.startswith("Bearer ") else ""
        if not admin:
            candidate = self.headers.get("x-api-key") or candidate
        expected = self.manager.admin_key if admin else self.manager.api_key
        if candidate and hmac.compare_digest(candidate.encode(), expected.encode()):
            return
        if not admin and candidate:
            scope = "models" if self.command == "GET" else "messages"
            self.caller_key, self.allowed_accounts = self.manager.api_keys.authenticate(candidate, scope, bool(self.headers.get("X-WebCC-Tools")))
            return
        raise Problem(401, "认证失败")

    def check_caller(self):
        if self.caller_key:
            self.manager.api_keys.check_active(self.caller_key)

    def body(self, limit=32 * 1024 * 1024):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise Problem(400, "Content-Length 无效")
        if self.headers.get("Transfer-Encoding"):
            raise Problem(400, "请求需使用 Content-Length")
        if length < 0 or length > limit:
            raise Problem(413, "请求体过大")
        self.connection.settimeout(30)
        try:
            body = self.rfile.read(length)
        except OSError:
            raise Problem(408, "读取客户端请求超时或断开")
        if len(body) != length:
            raise Problem(400, "请求体不完整")
        return body

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        self.request_id = uuid.uuid4().hex
        self.caller_key, self.allowed_accounts, self.retry_after, self.adapter = None, None, None, None
        try:
            target = urlsplit(self.path)
            if target.scheme or target.netloc or target.fragment:
                raise Problem(400, "请求地址必须是本站路径")
            route = target.path
            if self.command == "GET" and (route == "/" or route.startswith("/assets/")):
                name = "index.html" if route == "/" else route.lstrip("/")
                base = (ROOT / "static").resolve()
                path = (base / name).resolve()
                if base not in path.parents or not path.is_file():
                    raise Problem(404, "文件不存在")
                content_type = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}.get(path.suffix, "application/octet-stream")
                self.respond(200, path.read_bytes(), content_type)
            elif route == "/healthz" and self.command == "GET":
                self.respond(200, {"ok": True})
            elif route.startswith("/admin/"):
                self.authenticate(admin=True)
                if route == "/admin/api-keys" and self.command == "GET":
                    self.respond(200, {"keys": self.manager.api_keys.list()})
                elif route == "/admin/api-keys" and self.command == "POST":
                    self.respond(201, self.manager.api_keys.create(json.loads(self.body(16384))))
                elif re.fullmatch(r"/admin/api-keys/[a-f0-9]{12}/revoke", route) and self.command == "POST":
                    self.manager.api_keys.revoke(route.split("/")[3])
                    self.respond(200, {"ok": True})
                elif route == "/admin/accounts" and self.command == "GET":
                    self.respond(200, self.manager.snapshot())
                elif route == "/admin/accounts" and self.command == "POST":
                    fields = json.loads(self.body(1024 * 1024))
                    self.respond(201, {"id": self.manager.add(fields)})
                elif route == "/admin/proxy/test" and self.command == "POST":
                    self.respond(200, self.manager.test_proxy(json.loads(self.body(16384))))
                elif route == "/admin/update" and self.command == "POST":
                    if not self.manager.operations.acquire(blocking=False):
                        raise Problem(409, "管理操作正在进行")
                    self.manager.operations.release()
                    threading.Thread(target=self.update_background, daemon=True).start()
                    self.respond(202, {"ok": True})
                else:
                    detail = re.fullmatch(r"/admin/accounts/([a-f0-9]{12})/details", route)
                    if detail and self.command in {"GET", "POST"}:
                        identity = detail.group(1)
                        result = self.manager.account_details(identity) if self.command == "GET" else self.manager.update_account(identity, json.loads(self.body(16384)))
                        self.respond(200, result)
                        return
                    match = re.fullmatch(r"/admin/accounts/([a-f0-9]{12})/(pause|resume|disable|probe|proxy-test)", route)
                    if not match or self.command != "POST":
                        raise Problem(404, "接口不存在")
                    identity, action = match.groups()
                    result = self.manager.check_account_proxy(identity) if action == "proxy-test" else self.manager.probe(identity) if action == "probe" else self.manager.control(identity, action)
                    self.respond(200, result or {"ok": True})
            elif (self.command == "GET" and route in GET_ROUTES) or (self.command == "POST" and route in POST_ROUTES):
                self.authenticate()
                self.forward()
            else:
                raise Problem(404, "接口不存在")
        except (Problem, KeyProblem) as error:
            self.close_connection = True
            self.retry_after = getattr(error, "retry_after", None)
            self.respond(error.status, {"error": {"message": str(error), "type": "manager_error"}})
        except (ValueError, TypeError, AttributeError):
            self.close_connection = True
            self.respond(400, {"error": {"message": "请求格式无效"}})
        except (BrokenPipeError, ConnectionResetError, ClientGone):
            self.close_connection = True
        except Exception as error:
            print("request_failed", type(error).__name__, flush=True)
            self.close_connection = True
            self.respond(500, {"error": {"message": "管理服务内部错误，请查看脱敏日志"}})

    def update_background(self):
        self.manager.check_upstream(os.environ.get("MANAGER_FOLLOW", "master"), True)

    def forward(self):
        mode = self.headers.get("X-WebCC-Tools")
        if mode:
            if mode != "prompt-v1" or not self.manager.web_tools_enabled or self.command != "POST" or urlsplit(self.path).path != "/v1/messages":
                self.close_connection = True
                self.respond(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "Experimental tools are disabled or unsupported"}})
                return
            from web_tools.gateway import forward
            forward(self)
            return
        body = self.body() if self.command == "POST" else None
        if body:
            try:
                payload = json.loads(body)
            except (ValueError, RecursionError):
                payload = None
            reserved_model = isinstance(payload, dict) and payload.get("model") == "webcc-prompt-v1"
            if urlsplit(self.path).path == '/v1/messages':
                from web_documents import prepare, DocumentProblem
                try:
                    if prepare(payload):
                        body = json.dumps(payload, ensure_ascii=False).encode()
                except DocumentProblem as error:
                    raise Problem(400, str(error)) from None
            del payload
            if reserved_model:
                self.respond(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "prompt-v1 requires X-WebCC-Tools"}})
                return
        self.request_id = uuid.uuid4().hex
        self.request_deadline = time.monotonic() + self.manager.request_seconds
        attempted = set()
        failure = None
        for _ in range(self.manager.retry_attempts):
            if time.monotonic() >= self.request_deadline:
                failure = (504, "请求处理超时")
                break
            try:
                account = self.manager.acquire(exclude=attempted, request_deadline=self.request_deadline,
                                               on_wait=self.check_caller if self.caller_key else None, allowed=self.allowed_accounts)
            except Problem:
                if failure is None:
                    raise
                break
            attempted.add(account["id"])
            failure = self.forward_attempt(account, body)
            if failure is None:
                return
            print(json.dumps({"event": "upstream_retry", "request_id": self.request_id,
                              "account_id": account["id"], "attempt": len(attempted),
                              "status": failure[0], "reason": failure[1]}), flush=True)
        status, message = failure
        self.respond(status, {"error": {"message": message, "type": "upstream_error", "request_id": self.request_id, "attempts": len(attempted), "retryable": status in {429, 502, 503, 504} or status >= 500}})

    def forward_attempt(self, account, body):
        connection = http.client.HTTPConnection("127.0.0.1", account["port"], timeout=min(self.manager.worker_seconds, max(.1, self.request_deadline - time.monotonic())))
        sent, stream = False, None
        try:
            headers = {k: self.headers[k] for k in ("Content-Type", "anthropic-version", "anthropic-beta", "Accept") if k in self.headers}
            headers.update({"Authorization": "Bearer " + account["key"], "Accept-Encoding": "identity"})
            connection.request(self.command, self.path, body, headers)
            self.upstream_timeout(connection)
            response = connection.getresponse()
            if response.status >= 400:
                if response.status in {401, 403, 429} or response.status >= 500:
                    self.manager.record_failure(account, "HTTP " + str(response.status), response.status, response.getheader("Retry-After"))
                if response.status in {401, 403, 429} or response.status >= 500:
                    return response.status, "账号服务返回 HTTP " + str(response.status)
                self.respond(response.status, {"error": {"message": "账号服务返回 HTTP " + str(response.status), "type": "upstream_error"}})
                return
            if "text/event-stream" not in (response.getheader("Content-Type") or ""):
                chunks, size = [], 0
                while size <= 32 * 1024 * 1024:
                    self.upstream_timeout(connection)
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                if response.length not in (None, 0):
                    raise http.client.IncompleteRead(b"")
                data = b"".join(chunks)
                if len(data) > 32 * 1024 * 1024:
                    return 502, "上游响应超出大小限制"
                if self.command == "POST" and self.path.endswith("/chat/completions"):
                    try:
                        payload = json.loads(data)
                        choices = payload.get("choices", [])
                        valid = any(c.get("message", {}).get("content") or
                                    c.get("message", {}).get("tool_calls") or
                                    c.get("message", {}).get("refusal") or
                                    c.get("message", {}).get("reasoning_content") or
                                    c.get("finish_reason") == "content_filter" for c in choices)
                    except (ValueError, AttributeError, TypeError):
                        valid = False
                    if not valid:
                        self.manager.record_failure(account, "empty_response")
                        return 502, "上游未返回有效正文"
                if self.command == "POST" and self.path.endswith("/messages"):
                    try:
                        payload = json.loads(data)
                        valid = bool(payload.get("content")) or payload.get("stop_reason") == "refusal"
                    except (ValueError, AttributeError):
                        valid = False
                    if not valid:
                        self.manager.record_failure(account, "empty_response")
                        return 502, "上游未返回有效正文"
                try:
                    self.respond(response.status, data, response.getheader("Content-Type") or "application/json")
                except OSError:
                    raise ClientGone()
                if self.command == "POST":
                    self.manager.record_success(account)
                return
            stream, prefix, prefix_size = StreamState(), [], 0
            while not stream.meaningful and not stream.finished and not stream.failed:
                self.upstream_timeout(connection)
                chunk = response.read1(65536)
                if not chunk:
                    if response.length not in (None, 0):
                        raise http.client.IncompleteRead(b"")
                    break
                prefix.append(chunk)
                prefix_size += len(chunk)
                stream.feed(chunk)
                if prefix_size > 1024 * 1024:
                    return 502, "上游流未提供有效内容"
            if stream.failed:
                self.manager.record_failure(account, "stream_error", stream.error_status)
                if stream.error_status == 400:
                    self.respond(400, {"error": {"message": "上游拒绝请求参数", "type": "invalid_request_error"}})
                    return
                return stream.error_status, "上游流返回错误"
            if not stream.meaningful:
                self.manager.record_failure(account, "empty_response")
                return 502, "上游流未提供有效内容"
            try:
                self.send_response(response.status)
                for key in ("Content-Type", "Retry-After"):
                    if response.getheader(key):
                        self.send_header(key, response.getheader(key))
                self.send_header("X-Request-Id", self.request_id)
                self.send_header("Request-Id", self.request_id)
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
            except OSError:
                raise ClientGone()
            sent = True
            for chunk in prefix:
                self.write_stream(("%x\r\n" % len(chunk)).encode() + chunk + b"\r\n")
            while True:
                self.upstream_timeout(connection)
                chunk = response.read1(65536)
                if not chunk:
                    if response.length not in (None, 0):
                        raise http.client.IncompleteRead(b"")
                    break
                if stream:
                    stream.feed(chunk)
                self.write_stream(("%x\r\n" % len(chunk)).encode() + chunk + b"\r\n")
            if stream:
                if not stream.finished and stream.openai_chunk and self.path.endswith("/chat/completions") and not stream.failed:
                    terminal = b"data: [DONE]\n\n"
                    self.write_stream(("%x\r\n" % len(terminal)).encode() + terminal + b"\r\n")
                    stream.finished = True
                if stream.failed or not stream.finished:
                    raise http.client.HTTPException("Incomplete stream")
            self.write_stream(b"0\r\n\r\n")
            if self.command == "POST":
                self.manager.record_success(account)
        except ClientGone:
            self.close_connection = True
        except (OSError, http.client.HTTPException) as error:
            status = stream.error_status if stream and stream.failed else 504 if isinstance(error, TimeoutError) else 502
            self.manager.record_failure(account, "timeout" if status == 504 else "connection_or_stream_error", status)
            if not sent:
                return status, "账号通道连接或读取异常"
            self.close_connection = True
        finally:
            connection.close()
            self.manager.release(account)

    def upstream_timeout(self, connection):
        remaining = self.request_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Request deadline")
        if connection.sock:
            connection.sock.settimeout(min(self.manager.worker_seconds, remaining))

    def write_stream(self, data):
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except OSError:
            raise ClientGone()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, manager):
        self.manager = manager
        self.threads = threading.BoundedSemaphore(64)
        super().__init__(address, Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(30)
        return connection, address

    def process_request(self, request, address):
        if not self.threads.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.threads.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.threads.release()


def main():
    os.umask(0o077)
    manager = Manager(os.environ.get("MANAGER_DATA", "/var/lib/clewdr-manager"),
                      os.environ.get("MANAGER_IMAGE", "ghcr.io/xerxes-2/clewdr:v0.13.5"),
                      os.environ.get("CLEWDR_PASSWORD", ""), os.environ.get("CLEWDR_ADMIN_PASSWORD", ""),
                      int(os.environ.get("MANAGER_MAX_INFLIGHT", "4")), int(os.environ.get("MANAGER_QUEUE_SIZE", "16")),
                      float(os.environ.get("MANAGER_QUEUE_SECONDS", "15")))
    def maintenance():
        while True:
            interval = max(60, int(os.environ.get("MANAGER_UPDATE_SECONDS", "86400")))
            delay = manager.state["update"].get("checked_at", 0) + interval - time.time()
            if delay > 0:
                time.sleep(min(delay, 60))
                continue
            manager.check_upstream(os.environ.get("MANAGER_FOLLOW", "master"), os.environ.get("MANAGER_AUTO_UPDATE", "true").lower() == "true")
            time.sleep(60)
    threading.Thread(target=maintenance, daemon=True).start()
    bind = os.environ.get("MANAGER_BIND", "127.0.0.1")
    port = int(os.environ.get("MANAGER_PORT", "9000"))
    print("manager_listening", bind, port, flush=True)
    Server((bind, port), manager).serve_forever()


if __name__ == "__main__":
    main()
