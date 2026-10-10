import json
import unittest
from worker_errors import no_session


class WorkerErrorTests(unittest.TestCase):
    def test_only_known_worker_code_changes_failure_policy(self):
        body = json.dumps({'error': {'type': 'no_cookie_available', 'code': 500}}).encode()
        self.assertTrue(no_session(500, body))
        for status, raw in [(503, body), (500, b'no cookie available'),
                            (500, b'{"error":"no_cookie_available"}'),
                            (500, b'{"error":{"message":"no_cookie_available","type":"api_error"}}'),
                            (500, b'\xff'), (500, b'[]'), (500, body + b' ' * 65536)]:
            self.assertFalse(no_session(status, raw))
