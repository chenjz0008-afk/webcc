"""Web-account capability checks; never claim native sampling or signatures."""
import base64
import json
from pathlib import Path

TOOLS = [
    {'name': 'read_doc', 'input_schema': {'type': 'object', 'properties': {'id': {'enum': ['A']}},
        'required': ['id'], 'additionalProperties': False}},
    {'name': 'lookup_doc', 'input_schema': {'type': 'object', 'properties': {'query': {'type': 'object',
        'properties': {'id': {'enum': ['B']}, 'limit': {'type': 'integer', 'minimum': 2, 'maximum': 2},
                       'tags': {'type': 'array', 'items': {'enum': ['summary']}, 'minItems': 1, 'maxItems': 1}},
        'required': ['id', 'limit', 'tags'], 'additionalProperties': False}},
        'required': ['query'], 'additionalProperties': False}}
]


def run_basic_cases(send, fixtures):
    scenarios = [
        ('none', {'type': 'none'}, 'Read A, unless tool_choice forbids tools; then explain that it is disabled.', 0, 0, None),
        ('auto_text', {'type': 'auto'}, 'Say Hello. Do not read documents or request tools.', 0, 0, None),
        ('auto_tool', {'type': 'auto'}, 'Read A using read_doc.', 1, 1, 'read_doc'),
        ('any', {'type': 'any'}, 'Read A using read_doc.', 1, 8, None),
        ('selected', {'type': 'tool', 'name': 'lookup_doc'}, 'I prefer reading A, but follow tool_choice. The only legal lookup query has id B, limit 2 and tags ["summary"].', 1, 8, 'lookup_doc'),
        ('single', {'type': 'any', 'disable_parallel_tool_use': True}, 'Read A and lookup B (limit 2, tags ["summary"]). Respect the parallel restriction.', 1, 1, None),
        ('parallel', {'type': 'any'}, 'Request read_doc A and lookup_doc B (limit 2, tags ["summary"]) together in one response.', 2, 2, None)
    ]
    report = {'tool_choice': [], 'media': [], 'pass': True}
    for name, choice, prompt, low, high, selected in scenarios:
        result = send({'tools': TOOLS, 'messages': [{'role': 'user', 'content': prompt}], 'tool_choice': choice})
        calls = [block for block in result.get('message', {}).get('content', []) if block.get('type') == 'tool_use']
        ok = result['status'] == 200 and low <= len(calls) <= high and (not selected or all(c['name'] == selected for c in calls))
        report['tool_choice'].append({'name': name, 'pass': ok, 'response': result}); report['pass'] &= ok
    strict = send({'tools': [{**TOOLS[0], 'strict': True}], 'messages': [{'role': 'user', 'content': 'Read A'}]})
    calls = [b for b in strict.get('message', {}).get('content', []) if b.get('type') == 'tool_use']
    report['local_strict_accepted'] = strict['status'] == 200 and len(calls) == 1 and calls[0]['name'] == 'read_doc' and calls[0]['input'] == {'id': 'A'}
    report['native_strict_provided'] = False
    report['pass'] &= report['local_strict_accepted']
    fixtures = Path(fixtures)
    for name, kind, mime, path in [('image', 'image', 'image/png', 'blocks.png'),
                                  ('native_pdf', 'document', 'application/pdf', 'allocation.pdf'),
                                  ('legacy_pdf', 'image', 'application/pdf', 'allocation.pdf')]:
        prompt = ('Return only JSON {"colors":["color","color","color"]} describing the three blocks from left to right.' if name == 'image' else
                  'Read both pages of the attached PDF. Return only JSON with keys north, south and sum as integers, and references as the two reference strings found in page order. Do not invent data if the PDF is unavailable.')
        result = send({'experimental': False, 'streamed': False, 'model': 'claude-sonnet-4-6',
            'messages': [{'role': 'user', 'content': [{'type': kind, 'source': {'type': 'base64', 'media_type': mime,
                'data': base64.b64encode((fixtures / path).read_bytes()).decode()}}, {'type': 'text', 'text': prompt}]}]})
        text = ''.join(b.get('text', '') for b in result.get('message', {}).get('content', []))
        try:
            output = json.loads(text)
            expected = {'colors': ['red', 'blue', 'green']} if name == 'image' else {'north': 37, 'south': 83, 'sum': 120, 'references': ['ALLOCATION-001', 'ALLOCATION-002']}
            ok = output == expected
        except (ValueError, TypeError):
            output, ok = None, False
        report['pass'] &= result['status'] == 200 and ok
        report['media'].append({'name': name, 'pass': result['status'] == 200 and ok, 'status': result['status'],
                                'output': output, 'text_preview': text[:500]})
    return report
