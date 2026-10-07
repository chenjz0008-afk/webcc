"""Real synthetic Markdown edits, line citations, concurrent reads and denied reads."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import threading


def tool(name, description, fields, required):
    return {'name': name, 'description': description, 'input_schema': {
        'type': 'object', 'properties': fields, 'required': required, 'additionalProperties': False}}


TOOLS = [
    tool('read_document', 'Read a document and return paragraph IDs, numbered source lines and revision. MISSING does not exist; PRIVATE is denied.',
         {'id': {'type': 'string'}}, ['id']),
    tool('edit_paragraph', 'Replace one paragraph using its exact ID and current revision, then read the document back.',
         {'id': {'type': 'string'}, 'paragraph_id': {'type': 'string'}, 'revision': {'type': 'integer'}, 'text': {'type': 'string'}},
         ['id', 'paragraph_id', 'revision', 'text']),
    tool('submit_review', 'After read-back, submit exact quotes and line numbers for A-P2 and B-P2, plus observed read errors. Then summarize.',
         {'citations': {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {'type': 'object', 'properties': {
             'id': {'type': 'string'}, 'paragraph_id': {'type': 'string'}, 'line': {'type': 'integer'}, 'quote': {'type': 'string'}},
             'required': ['id', 'paragraph_id', 'line', 'quote'], 'additionalProperties': False}},
          'errors': {'type': 'array', 'items': {'enum': ['not_found', 'permission_denied']}}}, ['citations', 'errors'])
]


class Documents:
    def __init__(self, directory):
        self.directory = Path(directory); self.directory.mkdir(mode=0o700)
        self.lock = threading.Lock(); self.reads = set(); self.errors = set()
        self.updates = 0; self.readback = False; self.review = False
        self.intervals = []
        self.original = {}
        for identity in ('A', 'B'):
            text = f'''# Service handbook {identity}
Revision: 1

## Standard review
[{identity}-P1] Standard review records are retained for 30 days after closure.

## Priority review
[{identity}-P2] Priority review records are retained for 30 days after closure.

| Category | Owner | Days |
| --- | --- | --- |
| Standard | Operations | 30 |
| Priority | Audit | 30 |

- Preserve the audit trail.
  - Preserve the reference links.
- Keep all other retention policies unchanged.

See [retention reference][policy].

[policy]: https://example.invalid/retention "Retention policy"
'''
            self.path(identity).write_text(text); self.original[identity] = text
        self.expected = 'Priority review records are retained for 14 days after closure.'

    def path(self, identity):
        if identity not in ('A', 'B'):
            raise ValueError('Unrecognized document ID')
        return self.directory / (identity + '.md')

    def read(self, identity):
        import time
        start = time.monotonic()
        time.sleep(.03)  # Makes overlapping executor tasks measurable; not a model latency claim.
        with self.lock:
            self.reads.add(identity)
            self.intervals.append((start, time.monotonic()))
            if identity in ('MISSING', 'PRIVATE'):
                kind = 'not_found' if identity == 'MISSING' else 'permission_denied'
                self.errors.add(kind)
                return {'error': kind, 'id': identity}, True
            text = self.path(identity).read_text()
            if identity == 'B' and self.updates:
                self.readback = True
            paragraphs = [{'paragraph_id': match[1], 'line': number, 'text': match[2]}
                          for number, line in enumerate(text.splitlines(), 1)
                          if (match := re.fullmatch(r'\[([^]]+)\] (.*)', line))]
            return {'id': identity, 'revision': int(re.search(r'Revision: (\d+)', text)[1]),
                    'sha256': hashlib.sha256(text.encode()).hexdigest(), 'paragraphs': paragraphs,
                    'lines': [{'line': n, 'text': line} for n, line in enumerate(text.splitlines(), 1)]}, False

    def edit(self, fields):
        if not {'A', 'B', 'MISSING', 'PRIVATE'} <= self.reads:
            raise ValueError('Edit proposed before required reads')
        if fields != {'id': 'B', 'paragraph_id': 'B-P2', 'revision': 1, 'text': self.expected} or self.updates:
            raise ValueError('Wrong target, revision, replacement or duplicate edit; not executed')
        old = '[B-P2] Priority review records are retained for 30 days after closure.'
        text = self.path('B').read_text()
        if text.count(old) != 1:
            raise ValueError('Target is not unique')
        text = text.replace(old, '[B-P2] ' + self.expected).replace('Revision: 1\n', 'Revision: 2\n', 1)
        temporary = self.path('B').with_suffix('.tmp'); temporary.write_text(text); temporary.replace(self.path('B'))
        self.updates += 1
        return {'id': 'B', 'paragraph_id': 'B-P2', 'revision': 2}, False

    def submit(self, fields):
        if not self.readback or self.updates != 1 or set(fields['errors']) != {'not_found', 'permission_denied'}:
            raise ValueError('Review lacks read-back or accurate errors')
        if {(c['id'], c['paragraph_id']) for c in fields['citations']} != {('A', 'A-P2'), ('B', 'B-P2')}:
            raise ValueError('Wrong paragraph citation')
        for citation in fields['citations']:
            lines = self.path(citation['id']).read_text().splitlines()
            index = citation['line'] - 1
            expected = '[' + citation['paragraph_id'] + '] ' + citation['quote']
            if not 0 <= index < len(lines) or lines[index] != expected:
                raise ValueError('Quote or source line mismatch')
        self.review = True
        return {'accepted': True, 'citations': fields['citations'], 'errors': fields['errors']}, False

    def verify(self):
        expected = self.original['B'].replace('[B-P2] Priority review records are retained for 30 days after closure.',
                    '[B-P2] ' + self.expected).replace('Revision: 1\n', 'Revision: 2\n', 1)
        return self.path('A').read_text() == self.original['A'] and self.path('B').read_text() == expected


def run_document_case(send, directory, render=None):
    from experiments.web_tools import build_prompt
    store = Documents(directory)
    history = [{'role': 'user', 'content': 'First read A, B, MISSING and PRIVATE together in one response. Compare priority-review policies in A and B, not standard review. Change only paragraph B-P2 from 30 to 14 days; preserve A, B-P1, the table, list and reference links. Read B after editing. Call submit_review with exact quotes and source line numbers for A-P2 and updated B-P2 and both observed read error types. Do not fabricate contents for unavailable documents. Then give a concise summary including 30, 14, MISSING and PRIVATE.'}]
    report = {'steps': [], 'pass': False}
    try:
        for step in range(8):
            choice = {'type': 'any'} if step == 0 else {'type': 'auto'}
            response = send(build_prompt(TOOLS, history, choice))
            row = {'step': step, 'response': response}; report['steps'].append(row)
            if response.get('status') != 200:
                raise ValueError('SDK or upstream failure')
            content = response['message']['content']; calls = [b for b in content if b['type'] == 'tool_use']
            if step == 0 and (len(calls) != 4 or any(c['name'] != 'read_document' for c in calls) or {c['input']['id'] for c in calls} != {'A', 'B', 'MISSING', 'PRIVATE'}):
                raise ValueError('Initial four reads not followed')
            if not calls:
                answer = '\n'.join(b.get('text', '') for b in content)
                if not store.review or not all(word in answer for word in ('30', '14', 'MISSING', 'PRIVATE')):
                    raise ValueError('Summary lacks verified review or error disclosure')
                report['pass'] = True; break
            if any(c['name'] == 'submit_review' for c in calls) and len(calls) != 1:
                raise ValueError('Review must follow received read-back results in a separate turn')
            if all(c['name'] == 'read_document' for c in calls):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    outputs = list(pool.map(lambda c: store.read(c['input']['id']), calls))
            else:
                outputs = []
                for call in calls:
                    output = (store.read(call['input']['id']) if call['name'] == 'read_document' else
                              store.edit(call['input']) if call['name'] == 'edit_paragraph' else store.submit(call['input']))
                    outputs.append(output)
            results = [{'type': 'tool_result', 'tool_use_id': call['id'], 'is_error': error, 'content': json.dumps(output)}
                       for call, (output, error) in zip(calls, outputs)]
            results.reverse(); row['results'] = results
            history.extend([{'role': 'assistant', 'content': content}, {'role': 'user', 'content': results}])
        report.update(files_preserved=store.verify(), edits=store.updates, readback=store.readback,
                      citations_verified=store.review, observed_errors=sorted(store.errors),
                      concurrent_reads_overlap=any(a[0] < b[1] and b[0] < a[1] for i, a in enumerate(store.intervals) for b in store.intervals[i+1:]))
        if render:
            expected = {**store.original, 'B': store.original['B'].replace('[B-P2] Priority review records are retained for 30 days after closure.', '[B-P2] ' + store.expected).replace('Revision: 1\n', 'Revision: 2\n', 1)}
            report['markdown_render'] = render({'original': store.original, 'current': {key: store.path(key).read_text() for key in ('A', 'B')}, 'expected': expected})
            report['pass'] &= report['markdown_render']['pass']
        report['pass'] &= all([report['files_preserved'], report['edits'] == 1, report['readback'], report['citations_verified'], report['concurrent_reads_overlap']])
    except Exception as error:
        report['failure'] = type(error).__name__ + ': ' + str(error)[:160]
    return report
