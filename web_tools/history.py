"""Validation for the text-only experimental client-tool protocol."""
import json
import re
from jsonschema import Draft202012Validator

MAX_BYTES = 131072
MAX_DEPTH = 32


def bounded_json(value):
    def visit(item, depth):
        if depth > MAX_DEPTH:
            raise ValueError('Experimental context exceeds nesting limit')
        if isinstance(item, dict):
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
    visit(value, 0)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Experimental context exceeds byte limit')
    return encoded


def check_choice(choice, tools):
    if not isinstance(choice, dict) or set(choice) - {'type', 'name', 'disable_parallel_tool_use'}:
        raise ValueError('Unsupported tool choice')
    kind = choice.get('type')
    if kind not in ('auto', 'none', 'any', 'tool'):
        raise ValueError('Unsupported tool choice')
    if 'disable_parallel_tool_use' in choice and type(choice['disable_parallel_tool_use']) is not bool:
        raise ValueError('Parallel choice must be boolean')
    if kind == 'tool':
        if choice.get('name') not in {t['name'] for t in tools}:
            raise ValueError('Selected tool is undeclared')
    elif 'name' in choice:
        raise ValueError('Tool name requires selected-tool choice')


def check_history(history, tools):
    if not isinstance(history, list) or not history or len(history) > 128:
        raise ValueError('Experimental history requires 1 to 128 messages')
    schemas = {t['name']: t['input_schema'] for t in tools}
    pending, seen, previous = set(), set(), None
    for message in history:
        if not isinstance(message, dict) or set(message) != {'role', 'content'}:
            raise ValueError('Unsupported message fields')
        role = message['role']
        if role not in ('user', 'assistant') or role == previous or previous is None and role != 'user':
            raise ValueError('Experimental history must alternate user and assistant')
        content = message['content']
        blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else content
        if not isinstance(blocks, list) or not blocks:
            raise ValueError('Message content must be text or nonempty blocks')
        results, calls, text_seen = set(), set(), False
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError('Content block must be an object')
            kind = block.get('type')
            if kind == 'text':
                if set(block) != {'type', 'text'} or not isinstance(block['text'], str):
                    raise ValueError('Unsupported text block')
                text_seen = True
            elif kind == 'tool_use':
                if role != 'assistant' or set(block) != {'type', 'id', 'name', 'input'}:
                    raise ValueError('Unsupported tool call')
                identity = block['id']
                if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', identity) or identity in seen:
                    raise ValueError('Invalid or reused tool ID')
                name = block['name']
                if not isinstance(name, str) or name not in schemas or not isinstance(block['input'], dict):
                    raise ValueError('Unknown historical tool or invalid input')
                Draft202012Validator(schemas[name]).validate(block['input'])
                seen.add(identity)
                calls.add(identity)
            elif kind == 'tool_result':
                if role != 'user' or text_seen or set(block) - {'type', 'tool_use_id', 'content', 'is_error'}:
                    raise ValueError('Tool results must precede text in a user message')
                identity = block.get('tool_use_id')
                if not isinstance(identity, str) or identity not in pending or identity in results:
                    raise ValueError('Unmatched or duplicate tool result ID')
                if 'is_error' in block and type(block['is_error']) is not bool:
                    raise ValueError('Tool error flag must be boolean')
                output = block.get('content', '')
                if not isinstance(output, str):
                    if not isinstance(output, list) or any(not isinstance(b, dict) or set(b) != {'type', 'text'} or b.get('type') != 'text' or not isinstance(b.get('text'), str) for b in output):
                        raise ValueError('Experimental tool results support only text')
                results.add(identity)
            else:
                raise ValueError('Unsupported experimental content block')
        if role == 'user':
            if results != pending:
                raise ValueError('Every tool call needs an immediate result')
            pending = set()
        else:
            if len(calls) > 8:
                raise ValueError('Too many historical tool calls')
            pending = calls
        previous = role
    if pending or previous != 'user':
        raise ValueError('History must end with a user message and resolved tools')
