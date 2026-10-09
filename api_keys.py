"""Scoped caller credentials; upstream credentials stay in the account pool."""
from collections import deque
import hashlib
import hmac
import secrets
import time


class KeyProblem(Exception):
    def __init__(self, status, message, retry_after=None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


class ApiKeys:
    def __init__(self, manager):
        self.manager = manager
        self.records = manager.state.setdefault('api_keys', {})
        self.windows = {}

    def public(self, record):
        return {k: v for k, v in record.items() if k != 'hash'}

    def list(self):
        with self.manager.condition:
            self.manager.refresh()
            return [self.public(v) for v in self.records.values()]

    def create(self, fields):
        if not isinstance(fields, dict) or set(fields) - {'name', 'accounts', 'scopes', 'rpm', 'expires_at'}:
            raise KeyProblem(400, '密钥配置字段无效')
        name = fields.get('name', '')
        accounts = fields.get('accounts')
        scopes = fields.get('scopes', ['messages', 'models'])
        rpm = fields.get('rpm', 60)
        expiry = fields.get('expires_at')
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise KeyProblem(400, '密钥名称不能为空或超过 80 字符')
        if not isinstance(accounts, list) or not accounts or any(not isinstance(v, str) for v in accounts):
            raise KeyProblem(400, '必须指定密钥可以使用的账号')
        if not isinstance(scopes, list) or not scopes or any(v not in ['messages', 'models', 'experimental_tools', 'files'] for v in scopes):
            raise KeyProblem(400, '密钥功能权限无效')
        if type(rpm) is not int or not 1 <= rpm <= 1000:
            raise KeyProblem(400, 'RPM 必须在 1—1000 之间')
        if expiry is not None and (type(expiry) is not int or expiry <= time.time()):
            raise KeyProblem(400, '密钥到期时间必须是未来时间')
        with self.manager.condition:
            self.manager.refresh()
            if len(self.records) >= 128:
                raise KeyProblem(409, '密钥数量达到上限')
            if any(v not in self.manager.state['accounts'] for v in accounts):
                raise KeyProblem(400, '密钥指定的账号不存在')
            token = 'sk-webcc-' + secrets.token_urlsafe(32)
            identity = secrets.token_hex(6)
            record = {'id': identity, 'name': name.strip(), 'accounts': sorted(set(accounts)),
                      'scopes': sorted(set(scopes)), 'rpm': rpm, 'expires_at': expiry,
                      'hash': hashlib.sha256(token.encode()).hexdigest(), 'revoked': False,
                      'created_at': int(time.time())}
            self.records[identity] = record
            self.manager.save()
            return {**self.public(record), 'key': token}

    def revoke(self, identity):
        with self.manager.condition:
            self.manager.refresh()
            if identity not in self.records:
                raise KeyProblem(404, '调用密钥不存在')
            self.records[identity]['revoked'] = True
            self.windows.pop(identity, None)
            self.manager.save()

    def active(self, identity):
        if self.manager.cluster:
            try:
                record = self.manager.cluster.key(identity=identity)
            except Exception:
                raise KeyProblem(503, '中心鉴权状态不可用') from None
        else:
            record = self.records.get(identity)
        if not record or record['revoked'] or (record['expires_at'] is not None and record['expires_at'] <= time.time()):
            raise KeyProblem(401, '调用密钥已失效')
        return record

    def authenticate(self, token, scope, experimental=False):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.manager.condition:
            if self.manager.cluster:
                try:
                    record = self.manager.cluster.key(digest=digest)
                except Exception:
                    raise KeyProblem(503, '中心鉴权状态不可用') from None
            else:
                record = next((v for v in self.records.values() if hmac.compare_digest(v['hash'], digest)), None)
            if not record:
                raise KeyProblem(401, '认证失败')
            record = self.active(record['id'])
            if not hmac.compare_digest(record['hash'], digest):
                raise KeyProblem(401, '认证失败')
            if scope not in record['scopes'] or (experimental and 'experimental_tools' not in record['scopes']):
                raise KeyProblem(403, '调用密钥没有此功能权限')
            if self.manager.shared_limits:
                try:
                    retry = self.manager.shared_limits.admit(record['id'], record['rpm'])
                except Exception:
                    raise KeyProblem(503, '共享限流不可用，请稍后重试') from None
                if retry:
                    raise KeyProblem(429, '调用密钥请求速率超限', retry)
                return record['id'], frozenset(record['accounts'])
            now = time.monotonic()
            window = self.windows.setdefault(record['id'], deque())
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= record['rpm']:
                raise KeyProblem(429, '调用密钥请求速率超限', max(1, int(60 - (now - window[0])) + 1))
            window.append(now)
            return record['id'], frozenset(record['accounts'])

    def check_active(self, identity):
        with self.manager.condition:
            self.active(identity)
