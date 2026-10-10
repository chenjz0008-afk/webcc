"""WebCC history authentication; upstream signatures retain their own provenance."""
import base64
import hashlib
import hmac
import json
from cryptography.fernet import Fernet, InvalidToken
from web_files import FileProblem

PREFIX, TTL = 'webccsig_v1_', 86400


def cipher(manager, purpose):
    key = hashlib.sha256(('webcc:' + purpose + ':v1:' + manager.admin_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def digest(block):
    return hashlib.sha256(json.dumps({k: block.get(k) for k in ('type', 'thinking')},
                         sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def seal(manager, owner, block, model):
    if block.get('type') != 'thinking' or block.get('signature'):
        return block
    state = {'owner': owner, 'digest': digest(block), 'model': model, 'purpose': 'thinking'}
    return {**block, 'signature': PREFIX + cipher(manager, 'thinking').encrypt(json.dumps(state).encode()).decode()}


def verify(manager, owner, fields):
    for message in (fields.get('messages') or []) if isinstance(fields, dict) else []:
        blocks = message.get('content') if isinstance(message, dict) else None
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict) or block.get('type') != 'thinking':
                continue
            token = block.get('signature')
            if not isinstance(token, str) or not token.startswith(PREFIX):
                continue
            try:
                state = json.loads(cipher(manager, 'thinking').decrypt(token[len(PREFIX):].encode(), ttl=TTL))
                if state.get('owner') != owner or state.get('purpose') != 'thinking' or not hmac.compare_digest(state.get('digest', ''), digest(block)):
                    raise ValueError()
            except (InvalidToken, ValueError, TypeError, KeyError):
                raise FileProblem(400, 'WebCC thinking state is invalid, expired or belongs to another caller') from None


def message(manager, owner, result):
    if isinstance(result, dict) and result.get('type') == 'message':
        result['content'] = [seal(manager, owner, b, result.get('model', 'unknown')) if isinstance(b, dict) else b for b in result.get('content', [])]
    return result
