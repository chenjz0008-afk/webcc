import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


class RuntimeLimitTests(unittest.TestCase):
    def evaluate(self, values):
        environment = {k: v for k, v in os.environ.items() if not k.startswith('MANAGER_')}
        environment.update(values)
        return subprocess.run([sys.executable, '-c',
            'import json,runtime_limits as r;print(json.dumps([r.FILE_MAX,r.FILES_PER_KEY,r.DEFAULT_TTL,r.MEDIA_INFLIGHT]))'],
            cwd=Path(__file__).resolve().parents[1] / "backend", env=environment, capture_output=True, text=True)

    def test_valid_custom_limits(self):
        result = self.evaluate({'MANAGER_FILE_MAX_BYTES': '1024', 'MANAGER_FILES_PER_KEY': '3',
            'MANAGER_FILES_OWNER_BYTES': '3072', 'MANAGER_FILES_TOTAL_BYTES': '4096',
            'MANAGER_FILE_DEFAULT_TTL_SECONDS': '3600', 'MANAGER_MEDIA_MAX_INFLIGHT': '1'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [1024, 3, 3600, 1])

    def test_invalid_limits_fail_startup(self):
        cases = [{'MANAGER_FILE_MAX_BYTES': '0'}, {'MANAGER_FILE_MAX_BYTES': '20971521'},
            {'MANAGER_FILES_OWNER_BYTES': '1024'}, {'MANAGER_FILES_TOTAL_BYTES': '1'},
            {'MANAGER_FILE_DEFAULT_TTL_SECONDS': '3599'}, {'MANAGER_FILE_DEFAULT_TTL_SECONDS': '7776001'},
            {'MANAGER_MEDIA_MAX_INFLIGHT': '5'}, {'MANAGER_ATTACHMENT_MAX_COUNT': '17'},
            {'MANAGER_TOOL_REQUEST_MAX_BYTES': '131071'}, {'MANAGER_FILES_PER_KEY': '3.5'}]
        for values in cases:
            with self.subTest(values=values):
                result = self.evaluate(values)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('ValueError', result.stderr)


if __name__ == '__main__':
    unittest.main()
