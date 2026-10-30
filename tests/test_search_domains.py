import json
import types
import unittest
from unittest.mock import patch

from message_stream import event
from native_events import NativeEvents, restore
from search_domains import SearchDomains
from web_files import FileProblem


class SearchDomainTests(unittest.TestCase):
    def test_host_subdomain_path_and_blocked_precedence(self):
        policy = SearchDomains(['docs.python.org/3'], ['docs.python.org/3/private'], '')
        for url in ['https://docs.python.org/3', 'https://docs.python.org/3/library/csv.html',
                    'https://sub.docs.python.org/3/library/csv.html']:
            self.assertTrue(policy.accepts(url), url)
        for url in ['https://docs.python.org.attacker.org/3', 'https://other.org/3',
                    'https://docs.python.org/30', 'https://docs.python.org/3/private/a',
                    'https://docs.python.org/3/%70rivate/a', 'https://docs.python.org/3/foo/../private/a',
                    'https://user@docs.python.org/3', 'file:///3']:
            self.assertFalse(policy.accepts(url), url)

    def test_invalid_domains_and_optional_request_fields(self):
        for rule in ['https://docs.python.org', 'docs.python.org:443', 'u@docs.python.org',
                     'docs.python.org?q=x', '*.python.org', '-invalid.org', 'a..org']:
            with self.assertRaises(FileProblem):
                SearchDomains([rule], [], '')
        self.assertIsNone(SearchDomains.prepare({'tools': None, 'tool_choice': None}))
        fields = {'tools': [{'type': 'web_search_20250305', 'allowed_domains': ['docs.python.org']}],
                  'tool_choice': None, 'system': None, 'messages': [{'content': 'Find DictReader'}]}
        self.assertIsNotNone(SearchDomains.prepare(fields))
        self.assertIn('docs.python.org', fields['system'])
        fields['tool_choice'] = {'type': 'none'}
        self.assertIsNone(SearchDomains.prepare(fields))

    def run_stream(self, policy, thinking=True, with_text=True):
        manager = types.SimpleNamespace(admin_key='private-test-key', shared_limits=None)
        stream = NativeEvents(manager, 'owner', thinking=thinking, search_policy=policy)
        blocks = [
            {'type': 'thinking', 'thinking': 'Actual upstream reasoning'},
            {'type': 'tool_use', 'id': 'srv1', 'name': 'web_search', 'input': {'query': 'DictReader'}},
            {'type': 'tool_result', 'tool_use_id': 'srv1', 'name': 'web_search', 'content': [
                {'type': 'knowledge', 'url': 'https://docs.python.org/3/library/csv.html', 'title': 'CSV'},
                {'type': 'knowledge', 'url': 'https://outside.example/page', 'title': 'Outside'}]},
            {'type': 'text', 'text': ''}]
        raw = event('message_start', message={'id': 'msg1', 'model': 'test', 'content': [], 'usage': {}})
        if not with_text:
            blocks.pop()
        for index, block in enumerate(blocks):
            raw += event('content_block_start', index=index, content_block=block)
            if block['type'] == 'text':
                raw += event('content_block_delta', index=index,
                             delta={'type': 'text_delta', 'text': 'Unverified outside-source claim'})
            raw += event('content_block_stop', index=index)
        raw += event('message_delta', delta={'stop_reason': 'end_turn'}, usage={'output_tokens': 10})
        raw += event('message_stop')
        with patch('source_text.text', return_value={'url': 'https://docs.python.org/3/library/csv.html',
                                                   'text': 'Navigation\nDictReader uses the first row as fieldnames.'}) as fetch:
            output = b''.join(stream.feed(raw[i:i + 13]) for i in range(0, len(raw), 13))
        return manager, stream, output, fetch.call_count

    def test_filtered_sources_summary_stream_indices_and_ownership(self):
        for thinking in [True, False]:
            manager, stream, raw, calls = self.run_stream(SearchDomains(['docs.python.org'], [], 'DictReader'), thinking)
            self.assertTrue(stream.finished)
            self.assertNotIn(b'outside.example', raw)
            self.assertNotIn(b'Unverified outside-source claim', raw)
            self.assertIn(b'DictReader uses the first row', raw)
            self.assertEqual(calls, 1)
            result = next(b for b in stream.result['content'] if b['type'] == 'web_search_tool_result')
            self.assertEqual(len(result['content']), 1)
            with self.assertRaises(FileProblem):
                restore(manager, 'foreign', {'messages': [{'content': [result]}]})
            frames = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b'data: ')]
            starts = [f['index'] for f in frames if f['type'] == 'content_block_start']
            self.assertEqual(starts, list(range(len(starts))))

    def test_no_matching_sources_and_unfiltered_passthrough(self):
        _, stream, raw, calls = self.run_stream(SearchDomains(['never.example'], [], ''), False)
        self.assertEqual(calls, 0)
        self.assertIn(b'No actual search source matched', raw)
        self.assertNotIn(b'Unverified outside-source claim', raw)
        _, stream, raw, calls = self.run_stream(None, False)
        self.assertEqual(calls, 0)
        self.assertIn(b'Unverified outside-source claim', raw)
        self.assertIn(b'outside.example', raw)

    def test_search_result_only_receives_terminal_summary(self):
        for thinking in [True, False]:
            for domain in ['docs.python.org', 'never.example']:
                _, stream, raw, _ = self.run_stream(SearchDomains([domain], [], 'DictReader'), thinking, False)
                self.assertTrue(stream.finished)
                self.assertEqual(stream.result['content'][-1]['type'], 'text')
                self.assertTrue(stream.result['content'][-1]['text'])
                self.assertNotIn(b'outside.example', raw)

    def test_redirect_does_not_supply_outside_excerpt(self):
        policy = SearchDomains(['docs.python.org'], [], '')
        with patch('source_text.text', return_value={'url': 'https://outside.example', 'text': 'secret outside claim'}):
            summary = policy.summary(None, 'owner', {'a': {'url': 'https://docs.python.org/3', 'title': 'Docs'}})
        self.assertNotIn('secret outside claim', summary)

    def test_plain_source_is_bounded_and_does_not_run_html_parser(self):
        from unittest.mock import MagicMock
        from source_text import fetch
        for size in [100, 262145]:
            client = MagicMock()
            client.__enter__.return_value = client
            response = client.stream.return_value.__enter__.return_value
            response.is_redirect = False
            response.headers = {'content-type': 'text/plain; charset=utf-8'}
            response.iter_bytes.return_value = iter([b'x' * size])
            with patch.dict('os.environ', {'MANAGER_SOURCE_PROXY': 'socks5://proxy.example:1080'}), \
                    patch('source_text.httpx.Client', return_value=client), \
                    patch('source_text.target', return_value=('https://8.8.8.8', 'docs.python.org')), \
                    patch('source_text.subprocess.run') as parser:
                if size > 262144:
                    with self.assertRaises(FileProblem): fetch('https://docs.python.org/source.txt')
                else:
                    self.assertEqual(fetch('https://docs.python.org/source.txt')['text'], 'x' * size)
                parser.assert_not_called()

    def test_json_source_preserves_original_text_without_html_extraction(self):
        from unittest.mock import MagicMock
        from source_text import fetch
        raw = b'\xef\xbb\xbf{\n  "temperature": "31", "days": [1, 2]\n}\n'
        for mime in ('application/json; charset=utf-8', 'application/ld+json', 'APPLICATION/PROBLEM+JSON'):
            client = MagicMock()
            client.__enter__.return_value = client
            response = client.stream.return_value.__enter__.return_value
            response.is_redirect = False
            response.headers = {'content-type': mime}
            response.iter_bytes.return_value = iter([raw[:20], raw[20:]])
            with patch.dict('os.environ', {'MANAGER_SOURCE_PROXY': 'socks5://proxy.example:1080'}), \
                    patch('source_text.httpx.Client', return_value=client), \
                    patch('source_text.target', return_value=('https://8.8.8.8', 'example.com')), \
                    patch('source_text.subprocess.run') as parser:
                self.assertEqual(fetch('https://example.com/weather.json')['text'], raw.decode('utf-8-sig'))
                parser.assert_not_called()

    def test_json_source_rejects_invalid_and_oversized_content(self):
        from unittest.mock import MagicMock
        from source_text import fetch
        for raw, status in ((b'{"days":', 502), (b'"\xff"', 502), (b' ' * 262145, 413)):
            client = MagicMock()
            client.__enter__.return_value = client
            response = client.stream.return_value.__enter__.return_value
            response.is_redirect = False
            response.headers = {'content-type': 'application/json'}
            response.iter_bytes.return_value = iter([raw])
            with patch.dict('os.environ', {'MANAGER_SOURCE_PROXY': 'socks5://proxy.example:1080'}), \
                    patch('source_text.httpx.Client', return_value=client), \
                    patch('source_text.target', return_value=('https://8.8.8.8', 'example.com')), \
                    patch('source_text.subprocess.run') as parser:
                with self.assertRaises(FileProblem) as error:
                    fetch('https://example.com/weather.json')
                self.assertEqual(error.exception.status, status)
                parser.assert_not_called()
