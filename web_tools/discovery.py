"""Bounded caller-provided tool search and explicit reference expansion."""
import json
import re
from web_tools.history import bounded_json


def visible_tools(tools, history):
    names = {tool['name'] for tool in tools if not tool.get('defer_loading', False)}
    for message in history:
        for block in message.get('content', []) if isinstance(message.get('content'), list) else []:
            if not isinstance(block, dict) or block.get('type') != 'tool_result' or block.get('is_error'):
                continue
            for item in block.get('content', []) if isinstance(block.get('content'), list) else []:
                if isinstance(item, dict) and item.get('type') == 'tool_reference':
                    names.add(item.get('tool_name'))
    return [tool for tool in tools if tool['name'] in names]


def search(payload):
    from web_tools import check_tools
    if not isinstance(payload, dict) or set(payload) - {'tools', 'query', 'limit'}:
        raise ValueError('Search requires tools, query and optional limit')
    bounded_json(payload)
    tools, query, limit = payload.get('tools'), payload.get('query'), payload.get('limit', 5)
    check_tools(tools)
    if not isinstance(query, str) or not query.strip() or len(query) > 256 or type(limit) is not int or not 1 <= limit <= 16:
        raise ValueError('Invalid search query or limit')
    terms = set(re.findall(r'\w+', query.casefold()))
    scored = []
    for tool in tools:
        text = (tool['name'] + ' ' + tool.get('description', '') + ' ' + json.dumps(tool['input_schema'], ensure_ascii=False)).casefold()
        score = sum(term in text for term in terms) + (3 if tool['name'].casefold() == query.casefold().strip() else 0)
        if score:
            scored.append((score, tool['name'], tool))
    found = [tool for _, _, tool in sorted(scored, key=lambda row: (-row[0], row[1]))[:limit]]
    return {'tools': found, 'tool_references': [{'type': 'tool_reference', 'tool_name': t['name']} for t in found], 'count': len(found)}


def route(handler):
    handler.authenticate(scope='experimental_tools')
    if not handler.manager.web_tools_enabled:
        from manager import Problem
        raise Problem(400, 'Tool search is disabled')
    if handler.path != '/v1/tools/search':
        raise ValueError('Search query parameters are unsupported')
    handler.adapter = 'caller-tool-search-v1'
    handler.respond(200, search(json.loads(handler.body(131072))))
