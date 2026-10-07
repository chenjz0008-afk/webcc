"""Separate web attachments from bounded client-tool history."""
import base64
import binascii
import copy
from web_documents import prepare as prepare_documents
from web_files import FileProblem, validate_content

MAX_REQUEST = 32 * 1024 * 1024


def separate(payload):
    result = copy.deepcopy(payload)
    attachments = []
    total = 0

    def convert(block, role):
        nonlocal total
        kind = block.get('type')
        if kind == 'tool_result' and isinstance(block.get('content'), list):
            return {**block, 'content': [convert(item, role) if isinstance(item, dict) else item for item in block['content']]}
        if kind not in ('image', 'document'):
            return block
        if role != 'user':
            raise ValueError('Attachments require user content')
        if kind == 'document':
            wrapper = {'messages': [{'role': 'user', 'content': [block]}]}
            prepare_documents(wrapper)
            converted = wrapper['messages'][0]['content']
            if converted[-1]['type'] == 'text':
                return converted[-1]
            prefix = '\n'.join(b['text'] for b in converted[:-1])
            image = converted[-1]
        else:
            if set(block) != {'type', 'source'}:
                raise ValueError('Unsupported image fields')
            prefix, image = '', block
        source = image.get('source')
        if not isinstance(source, dict) or set(source) != {'type', 'media_type', 'data'} or source['type'] != 'base64' or not isinstance(source['data'], str):
            raise ValueError('Attachments require inline base64 or owned file IDs')
        try:
            raw = base64.b64decode(source['data'], validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Invalid attachment base64') from None
        mime = source['media_type']
        if kind == 'image' and (not isinstance(mime, str) or not mime.startswith('image/')):
            raise ValueError('Image source must use an image MIME type')
        validate_content(mime, raw)
        total += len(source['data'])
        if total > MAX_REQUEST or len(attachments) >= 32:
            raise FileProblem(413, 'Attachments exceed request limits')
        identity = 'attachment_' + str(len(attachments) // 2 + 1)
        label = identity + ' (' + mime + ')' + ('\n' + prefix if prefix else '')
        attachments.extend([{'type': 'text', 'text': 'Attached content for ' + label}, image])
        return {'type': 'text', 'text': '[See attached content: ' + label + ']'}

    for message in result.get('messages', []) if isinstance(result, dict) else []:
        if isinstance(message, dict) and isinstance(message.get('content'), list):
            message['content'] = [convert(block, message.get('role')) if isinstance(block, dict) else block for block in message['content']]
    return result, attachments
