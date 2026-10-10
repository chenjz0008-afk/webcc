"""Configured node endpoints; caller headers never cross the node boundary."""
import http.client
import ipaddress
import json
from pathlib import Path
import ssl
from urllib.parse import urlsplit


def semantic_headers(headers):
    allowed = {'content-type', 'anthropic-version', 'anthropic-beta', 'accept'}
    values = {k.lower(): v for k, v in (headers or {}).items() if k.lower() in allowed}
    if any(not isinstance(v, str) or len(v) > 4096 or '\r' in v or '\n' in v for v in values.values()):
        raise ValueError('Invalid protocol header')
    return values


class NodeConnection:
    def __init__(self, endpoint, account, reservation, timeout, on_dispatch=None, on_rejection=None, profile=None):
        url = urlsplit(endpoint['url'])
        self.prefix = url.path.rstrip('/')
        self.account, self.reservation, self.token = account, reservation, endpoint['token']
        self.on_dispatch = on_dispatch
        self.on_rejection = on_rejection
        self.profile = profile
        if url.scheme == 'https':
            context = endpoint.get('_tls_context')
            if context is None:
                context = ssl.create_default_context(cafile=endpoint.get('ca'))
                if endpoint.get('client_cert'):
                    context.load_cert_chain(endpoint['client_cert'], endpoint['client_key'])
            self.connection = http.client.HTTPSConnection(url.hostname, url.port, timeout=timeout, context=context)
        else:
            self.connection = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)

    @property
    def sock(self):
        return self.connection.sock

    def request(self, method, path, body=None, headers=None):
        forwarded = semantic_headers(headers)
        if self.on_dispatch:
            self.on_dispatch()
        self.connection.request(method, self.prefix + '/internal/forward', body, {
            'content-type': 'application/json', **forwarded, 'Authorization': 'Bearer ' + self.token,
            'X-WebCC-Account': self.account, 'X-WebCC-Reservation': self.reservation,
            'X-WebCC-Route': path,
            **({'X-WebCC-Worker-Profile': self.profile} if self.profile else {}),
        })

    def getresponse(self):
        response = self.connection.getresponse()
        if response.getheader('X-WebCC-Node-Error') == '1' and self.on_rejection:
            self.on_rejection()
        return response

    def close(self):
        self.connection.close()


def endpoints(path):
    source = Path(path)
    if source.stat().st_mode & 0o077:
        raise ValueError('Node credentials file must be private (0600 or 0400)')
    records = json.loads(source.read_text())
    if not isinstance(records, dict):
        raise ValueError('Node configuration must be an object')
    for identity, endpoint in records.items():
        if not isinstance(identity, str) or not identity or not isinstance(endpoint, dict):
            raise ValueError('Invalid node configuration')
        if set(endpoint) - {'url', 'token', 'ca', 'client_cert', 'client_key'}:
            raise ValueError('Unsupported node configuration fields')
        url = urlsplit(endpoint.get('url', ''))
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('Node endpoint must be a root HTTP(S) URL')
        token = endpoint.get('token')
        if not isinstance(token, str) or len(token) < 32 or any(ord(c) <= 32 for c in token):
            raise ValueError('Node token must contain at least 32 characters')
        if bool(endpoint.get('client_cert')) != bool(endpoint.get('client_key')):
            raise ValueError('Both node client certificate and key are required')
        if url.scheme == 'http':
            try:
                if not ipaddress.ip_address(url.hostname).is_loopback:
                    raise ValueError('Plain HTTP is limited to literal loopback addresses')
            except ValueError:
                raise ValueError('Remote nodes require verified TLS') from None
        elif not endpoint.get('ca') or not endpoint.get('client_cert'):
            raise ValueError('TLS nodes require a CA and client certificate')
        else:
            context = ssl.create_default_context(cafile=endpoint['ca'])
            context.load_cert_chain(endpoint['client_cert'], endpoint['client_key'])
            endpoint['_tls_context'] = context
    return records
