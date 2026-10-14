"""Constrain returned web sources without claiming control of upstream retrieval."""
import re
from posixpath import normpath
from urllib.parse import unquote, urlsplit
from web_files import FileProblem


def rules(values):
    if not isinstance(values, list) or len(values) > 128:
        raise FileProblem(400, 'Search domain filters must be hostname lists')
    result = []
    for value in values:
        if not isinstance(value, str) or len(value) > 2048 or not value.isascii():
            raise FileProblem(400, 'Search domains require ASCII hostnames')
        parsed = urlsplit('//' + value)
        host = parsed.hostname or ''
        if (not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) or
                '..' in host or ':' in parsed.netloc or parsed.username or parsed.query or parsed.fragment or
                any(not label or label.startswith('-') or label.endswith('-') for label in host.split('.'))):
            raise FileProblem(400, 'Search domains cannot contain schemes, ports or credentials')
        result.append((host, normpath(unquote(parsed.path)) if parsed.path else ''))
    return result


class SearchDomains:
    def __init__(self, allowed, blocked, query):
        self.allowed, self.blocked, self.query = rules(allowed), rules(blocked), query

    @classmethod
    def prepare(cls, fields):
        tool = next((t for t in (fields.get('tools') or []) if isinstance(t, dict) and
                     str(t.get('type', '')).startswith('web_search_') and
                     (t.get('allowed_domains') or t.get('blocked_domains'))), None)
        choice = fields.get('tool_choice')
        if tool is None or isinstance(choice, dict) and choice.get('type') == 'none':
            return None
        query = ' '.join(b.get('text', '') for m in fields.get('messages', [])[-1:] if isinstance(m, dict)
                         for b in ([{'text': m['content']}] if isinstance(m.get('content'), str)
                                   else (m.get('content') or [])) if isinstance(b, dict) and isinstance(b.get('text'), str))
        policy = cls(tool.get('allowed_domains', []), tool.get('blocked_domains', []), query)
        instruction = ('For web searches, include these source constraints in the search query. '
                       'Return genuine matching sources; do not substitute outside domains. Allowed: ' +
                       ', '.join(h + p for h, p in policy.allowed) + '; blocked: ' +
                       ', '.join(h + p for h, p in policy.blocked))
        system = fields.get('system') or ''
        if not isinstance(system, (str, list)):
            raise FileProblem(400, 'System must be text or content blocks')
        fields['system'] = ([*system, {'type': 'text', 'text': instruction}] if isinstance(system, list)
                            else system + '\n' + instruction)
        return policy

    def accepts(self, url):
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or '').lower().rstrip('.')
            if parsed.scheme not in {'http', 'https'} or not host or parsed.username or parsed.password:
                return False
            def matches(rule):
                domain, path = rule
                candidate = normpath(unquote(parsed.path))
                return (host == domain or host.endswith('.' + domain)) and (
                    not path or path == '/' or candidate == path or candidate.startswith(path + '/'))
            return (not self.allowed or any(map(matches, self.allowed))) and not any(map(matches, self.blocked))
        except (TypeError, ValueError):
            return False

    def summary(self, manager, owner, sources):
        sources = {url: source for url, source in sources.items() if self.accepts(source['url'])}
        if not sources:
            return 'No actual search source matched the requested domain filters. Do not substitute other domains.'
        lines = ['Actual search sources matching the requested domain filters:']
        terms = set(re.findall(r'[a-z][a-z0-9_]{3,}', self.query.lower())) - {'perform', 'search', 'query', 'documentation', 'please', 'with', 'that', 'this'}
        ordered = sorted(sources.values(), key=lambda s: not urlsplit(s['url']).path.endswith('.html'))
        for n, source in enumerate(ordered):
            lines.append('- ' + source.get('title', '') + ': ' + source['url'])
            if n >= 1:
                continue
            try:
                from source_text import text
                page = text(manager, owner, source['url'])
                if not self.accepts(page['url']):
                    continue
                original = page['text']
                lines.append('Verified page excerpt: ' + self.excerpt(original, terms))
            except FileProblem:
                lines.append('Page text unavailable; this search-provided URL has not been verified.')
        return '\n'.join(lines)

    @staticmethod
    def excerpt(original, terms):
        paragraphs = [p.strip() for p in original.split('\n') if p.strip()]
        if not paragraphs:
            return ''
        index = max(range(len(paragraphs)), key=lambda i: sum(t in paragraphs[i].lower() for t in terms))
        return '\n'.join(paragraphs[index:index + 8])[:1000]
