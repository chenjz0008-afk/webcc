import json
import unittest
from unittest.mock import patch
from request_timing import CURRENT, Timing, measure


class TimingTests(unittest.TestCase):
    def test_heartbeat_and_empty_thinking_do_not_count_as_content(self):
        timing = Timing('/v1/messages')
        for value in ({'type': 'ping'}, {'type': 'message_start', 'message': {}},
                      {'type': 'content_block_start', 'content_block': {'type': 'thinking', 'thinking': ''}}):
            timing.client(b'data: ' + json.dumps(value).encode() + b'\n\n')
        self.assertEqual(timing.firsts, {})
        packet = b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"private prompt"}}\n\n'
        for byte in packet:
            timing.client(bytes([byte]))
        self.assertIn('client_content', timing.firsts)
        first = timing.firsts['client_content']
        timing.client(packet)
        self.assertEqual(timing.firsts['client_content'], first)
        with patch('builtins.print') as output:
            timing.finish('request-id')
        record = json.loads(output.call_args.args[0])
        self.assertNotIn('private prompt', json.dumps(record))

    def test_explicit_thread_timing_and_failed_stage_are_recorded(self):
        timing = Timing('/v1/messages')
        with self.assertRaises(ValueError), measure('worker_prepare', timing):
            raise ValueError('fixture')
        self.assertGreaterEqual(timing.stages['worker_prepare'], 0)
        timing.observe('upstream', {'type': 'content_block_delta', 'delta': {'type': 'thinking_delta', 'thinking': 'summary'}})
        self.assertIn('upstream_content', timing.firsts)
        timing.client(b'data: {"type":"error","error":{"message":"fixture"}}\n\n')
        self.assertTrue(timing.stream_error)
        self.assertIsNone(getattr(CURRENT, 'timing', None))
