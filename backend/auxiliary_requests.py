"""Bounded adaptation of Claude Code's explicit severity review protocol."""
import re


def is_classifier(fields):
    system = fields.get('system', [])
    system = [system] if isinstance(system, str) else system
    texts = [b.get('text', '') if isinstance(b, dict) else b for b in system] if isinstance(system, list) else []
    return (not fields.get('stream') and not fields.get('tools') and fields.get('thinking') == {'type': 'disabled'} and
            type(fields.get('max_tokens')) is int and 1 <= fields['max_tokens'] <= 128 and
            fields.get('stop_sequences') == ['</severity>'] and
            any(isinstance(s, str) and s.startswith('You are a security monitor for autonomous AI coding agents.') for s in texts))


def prepare(fields):
    # The web transport spends its ceiling on reasoning even with thinking disabled.
    return {**fields, 'max_tokens': max(fields['max_tokens'], 1024),
            'output_config': {**fields.get('output_config', {}), 'effort': 'low'}}


def validate(message):
    text = ''.join(b.get('text', '') for b in message.get('content', []) if b.get('type') == 'text')
    if message.get('stop_reason') == 'stop_sequence' and message.get('stop_sequence') == '</severity>' and not text.rstrip().endswith('</severity>'):
        text += '</severity>'
    match = re.fullmatch(r'\s*<severity>([0-9]{1,3})</severity>(?:\s*<category>[A-Za-z0-9 ]{1,128}</category>)?\s*', text)
    if message.get('stop_reason') in {'max_tokens', 'refusal'} or not match or int(match[1]) > 100:
        raise ValueError('Classifier did not return a complete severity verdict')
