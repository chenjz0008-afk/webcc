"""Bounded adaptation of Claude Code's explicit permission review protocols."""
import re


def full_review(fields):
    messages = fields.get('messages')
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict):
        return False
    content = messages[-1].get('content', '')
    text = content if isinstance(content, str) else ''.join(b.get('text', '') for b in content if isinstance(b, dict)) if isinstance(content, list) else ''
    return (not fields.get('stop_sequences') and type(fields.get('max_tokens')) is int and
            256 <= fields['max_tokens'] <= 8192 and '<transcript>' in text and '</transcript>' in text and
            'Review the classification process and follow it carefully' in text and
            'Use <thinking> before responding with <block>.' in text)


def is_classifier(fields):
    system = fields.get('system', [])
    system = [system] if isinstance(system, str) else system
    texts = [b.get('text', '') if isinstance(b, dict) else b for b in system] if isinstance(system, list) else []
    return (not fields.get('stream') and not fields.get('tools') and fields.get('thinking') == {'type': 'disabled'} and
            (type(fields.get('max_tokens')) is int and 1 <= fields['max_tokens'] <= 128 and
             fields.get('stop_sequences') in (['</severity>'], ['</block>']) or full_review(fields)) and
            any(isinstance(s, str) and s.startswith('You are a security monitor for autonomous AI coding agents.') for s in texts))


def prepare(fields):
    # The web transport spends its ceiling on reasoning even with thinking disabled.
    return {**fields, 'max_tokens': max(fields['max_tokens'], 1024),
            'output_config': {**fields.get('output_config', {}), 'effort': 'low'}}


def validate(message, delimiter='</severity>', full=False):
    text = ''.join(b.get('text', '') for b in message.get('content', []) if b.get('type') == 'text')
    if full:
        text = re.sub(r'^\s*<thinking>.*?</thinking>\s*', '', text, count=1, flags=re.DOTALL)
    if message.get('stop_reason') == 'stop_sequence' and message.get('stop_sequence') == delimiter and not text.rstrip().endswith(delimiter):
        text += delimiter
    pattern = (r'\s*<block>(yes|no)</block>(?:\s*<category>[A-Za-z0-9 ]{1,128}</category><reason>[^<>]{1,2048}</reason>)?\s*'
               if delimiter == '</block>' else
               r'\s*<severity>([0-9]{1,3})</severity>(?:\s*<category>[A-Za-z0-9 ]{1,128}</category>)?\s*')
    match = re.fullmatch(pattern, text)
    if message.get('stop_reason') in {'max_tokens', 'refusal'} or not match or delimiter == '</severity>' and int(match[1]) > 100:
        raise ValueError('Classifier did not return a complete permission verdict')
    if delimiter == '</block>' and match[1] == 'no' and '<category>' in text:
        raise ValueError('Allow verdict must not contain block metadata')
    if full and match[1] == 'yes' and ('<category>' not in text or '<reason>' not in text):
        raise ValueError('Full review block verdict requires a rule and reason')
