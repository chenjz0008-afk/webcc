import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from node_transport import endpoints, NodeConnection


class NodeTransportTests(unittest.TestCase):
    def parse(self, endpoint):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nodes.json'
            path.write_text(json.dumps({'node-1': endpoint})); path.chmod(0o600)
            return endpoints(path)

    def test_plaintext_only_loopback_and_no_url_credentials(self):
        token = 'x' * 40
        self.parse({'url': 'http://127.0.0.1:9010', 'token': token})
        for url in ['http://192.0.2.1:9010', 'http://localhost:9010', 'http://user:pass@127.0.0.1', 'http://127.0.0.1/path', 'http://127.0.0.1/?token=x']:
            with self.assertRaises(ValueError): self.parse({'url': url, 'token': token})
        with self.assertRaises(ValueError): self.parse({'url': 'https://example.com', 'token': token})
        with self.assertRaises(ValueError): self.parse({'url': 'http://127.0.0.1', 'token': 'short'})

    def test_forwarding_replaces_caller_identity_headers(self):
        with patch('node_transport.http.client.HTTPConnection') as transport:
            connection = NodeConnection({'url': 'http://127.0.0.1:9010', 'token': 'node-secret'}, 'account-id', 'reservation-id', 5)
            connection.request('POST', '/v1/messages', b'{}', {'Authorization': 'Bearer caller-secret', 'X-Forwarded-For': 'caller-address', 'User-Agent': 'caller-host-marker'})
            args = transport.return_value.request.call_args.args
            self.assertEqual(args[1], '/internal/forward')
            self.assertNotIn('caller-secret', str(args))
            self.assertNotIn('caller-address', str(args))
            self.assertNotIn('caller-host-marker', str(args))
            self.assertEqual(args[3]['X-WebCC-Reservation'], 'reservation-id')

    def test_only_explicit_node_rejection_releases_unclaimed_task(self):
        calls = []
        with patch('node_transport.http.client.HTTPConnection') as transport:
            connection = NodeConnection({'url': 'http://127.0.0.1:9010', 'token': 'node-secret'},
                'account-id', 'reservation-id', 5, on_dispatch=lambda: calls.append('dispatch'),
                on_rejection=lambda: calls.append('rejected'))
            self.assertEqual(calls, [])
            connection.request('POST', '/v1/messages', b'{}')
            self.assertEqual(calls, ['dispatch'])
            transport.return_value.getresponse.return_value.getheader.return_value = None
            connection.getresponse()
            self.assertEqual(calls, ['dispatch'])
            transport.return_value.getresponse.return_value.getheader.return_value = '1'
            connection.getresponse()
            self.assertEqual(calls, ['dispatch', 'rejected'])


if __name__ == '__main__': unittest.main()
