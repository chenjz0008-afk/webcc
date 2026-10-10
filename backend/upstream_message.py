"""Read native web SSE before the lossy ClewdR nonstream conversion."""
import json


class UnexpectedTool(ValueError):
    pass


def read(response, thinking=False, on_event=None):
    if 'text/event-stream' not in response.getheader('Content-Type', ''):
        raw = response.read(1048577)
        if len(raw) > 1048576 or response.length not in (None, 0):
            raise ValueError('Upstream response exceeds limit')
        value = json.loads(raw)
        if isinstance(value, dict) and isinstance(value.get('content'), list):
            if any(b.get('type') not in {'text', 'thinking', 'redacted_thinking'} for b in value['content']):
                raise UnexpectedTool('Unexpected built-in tool execution in client-tool mode')
            if thinking:
                value['_webcc_thinking'] = [b for b in value['content'] if b.get('type') in {'thinking', 'redacted_thinking'}]
                value['content'] = [b for b in value['content'] if b.get('type') not in {'thinking', 'redacted_thinking'}]
                raw = json.dumps(value, ensure_ascii=False).encode()
        return raw
    message, blocks, thoughts, completed, size = None, {}, {}, False, 0
    for line in response:
        size += len(line)
        if size > 1048576 or len(line) > 131072:
            raise ValueError('Upstream stream exceeds limit')
        if not line.startswith(b'data: '):
            continue
        event = json.loads(line[6:])
        if on_event:
            on_event(event)
        kind = event.get('type')
        if kind == 'error':
            raise ValueError('Upstream stream failed')
        if kind == 'message_start':
            if message is not None:
                raise ValueError('Repeated message start')
            message = event['message']
        elif kind == 'content_block_start':
            block = event['content_block']
            if block.get('type') not in {'text', 'thinking', 'redacted_thinking'}:
                raise UnexpectedTool('Unexpected built-in tool execution in client-tool mode')
            if block['type'] == 'text':
                blocks[event['index']] = {'type': 'text', 'text': block.get('text', '')}
            elif thinking:
                thoughts[event['index']] = dict(block)
        elif kind == 'content_block_delta' and event.get('delta', {}).get('type') == 'text_delta':
            blocks[event['index']]['text'] += event['delta']['text']
        elif kind == 'content_block_delta' and thinking and event['index'] in thoughts:
            delta = event['delta']
            if delta.get('type') in {'thinking_delta', 'signature_delta'}:
                field = 'thinking' if delta['type'] == 'thinking_delta' else 'signature'
                thoughts[event['index']][field] = thoughts[event['index']].get(field, '') + delta[field]
        elif kind == 'message_delta':
            if message is None:
                raise ValueError('Missing message start')
            message.update(event.get('delta', {}))
            if 'usage' in event:
                message.setdefault('usage', {}).update(event['usage'])
        elif kind == 'message_stop':
            completed = True
            break
    if not message or not completed or message.get('stop_reason') not in {'end_turn', 'max_tokens', 'refusal', 'stop_sequence'}:
        raise ValueError('Incomplete upstream message')
    message['content'] = [blocks[i] for i in sorted(blocks)]
    if thoughts:
        message['_webcc_thinking'] = [thoughts[i] for i in sorted(thoughts)]
    return json.dumps(message, ensure_ascii=False).encode()
