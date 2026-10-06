import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import egress
from manager import Manager, Problem


class EgressTests(unittest.TestCase):
    def test_only_public_literal_proxy(self):
        for proxy in ['http://127.0.0.1:80', 'socks5h://example.com:1080', 'http://[::1]:80',
                      'http://192.168.1.2:80', 'http://8.8.8.8', 'file://8.8.8.8:80']:
            with self.assertRaises(egress.EgressError):
                egress.endpoint(proxy)
        self.assertEqual(egress.endpoint('socks5h://user:secret@8.8.8.8:1080'), ('8.8.8.8', 1080))

    def test_atomic_rules_are_narrow(self):
        rules = egress.rules({'a' * 12: {'ip': '8.8.8.8', 'port': 1080}})
        self.assertIn('-i wcaaaaaaaaaaaa -d 8.8.8.8/32 -p tcp --dport 1080 -j ACCEPT', rules)
        self.assertIn('--ctdir REPLY', rules)
        self.assertIn('-A WEBCC-OUT -i wc+ -j DROP', rules)
        self.assertIn('-A WEBCC-HOST -i wc+ -j DROP', rules)
        self.assertNotIn('--dport 53', rules)
        self.assertNotIn('-A WEBCC-HOST -i wcaaaaaaaaaaaa', rules)
        self.assertTrue(rules.endswith('COMMIT\n'))
        ipv6 = egress.rules({}, True)
        self.assertNotIn('ACCEPT', ipv6)
        self.assertIn('-i wc+ -j DROP', ipv6)

    def test_invalid_identity_or_policy_rejected(self):
        with self.assertRaises(egress.EgressError):
            egress.names('injected\n-A INPUT -j ACCEPT')
        with self.assertRaises(egress.EgressError):
            egress.rules({'a' * 12: {'ip': '1.1.1.1 -j ACCEPT', 'port': 80}})

    def test_policy_persisted_before_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            def inspect(policies):
                self.assertEqual(json.loads((Path(temp) / 'egress.json').read_text()), policies)
                self.assertEqual((Path(temp) / 'egress.json').stat().st_mode & 0o777, 0o600)
            with patch.object(egress, 'apply', side_effect=inspect):
                egress.locked(temp, {'a' * 12: {'ip': '8.8.8.8', 'port': 1080}})

    def test_firewall_failure_prevents_network_creation(self):
        with patch.object(egress, 'locked', side_effect=egress.EgressError('blocked')), patch.object(egress, 'run') as command:
            with self.assertRaises(egress.EgressError):
                egress.prepare('/tmp', 'a' * 12, 'http://8.8.8.8:1080')
            command.assert_not_called()

    def test_socks_dns_uses_proxy_and_config_waits_for_firewall(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'MANAGER_PROXY_ONLY': 'true'}):
            manager = Manager(temp, 'image', 'api', 'admin')
            path = Path(temp) / 'clewdr.toml'
            original = 'proxy = "socks5://u:p@8.8.8.8:1080"\nmax_retries = 5\n'
            path.write_text(original)
            account = {'id': 'a' * 12, 'directory': temp}
            with patch.object(egress, 'prepare', side_effect=egress.EgressError('blocked')):
                with self.assertRaises(Problem):
                    manager.worker_network(account)
            self.assertEqual(path.read_text(), original)
            with patch.object(egress, 'prepare', return_value=['--network', 'test']) as prepare:
                self.assertEqual(manager.worker_network(account), ['--network', 'test'])
                self.assertEqual(prepare.call_args.args[2], 'socks5h://u:p@8.8.8.8:1080')
            self.assertIn('socks5h://', path.read_text())
            self.assertIn('max_retries = 5', path.read_text())


if __name__ == '__main__':
    unittest.main()
