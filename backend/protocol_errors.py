"""Claude-style error envelopes for the public Messages and resource routes."""
import re
from email.utils import parsedate_to_datetime

ERROR_TYPES = {400: 'invalid_request_error', 401: 'authentication_error', 402: 'billing_error',
               403: 'permission_error', 404: 'not_found_error', 409: 'conflict_error',
               413: 'request_too_large', 429: 'rate_limit_error', 500: 'api_error',
               504: 'timeout_error', 529: 'overloaded_error'}


def retry_after(value):
    if not isinstance(value, str) or len(value) > 128 or '\r' in value or '\n' in value:
        return None
    value = value.strip()
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value):
        return value
    try:
        return value if parsedate_to_datetime(value).tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def applies(path):
    route = path.split('?', 1)[0]
    for prefix in ['/v1/messages', '/code/v1/messages', '/v1/models', '/code/v1/models', '/v1/files', '/v1/skills', '/v1/runs', '/v1/tools/search', '/v1/mcp', '/v1/network']:
        if route == prefix or route.startswith(prefix + '/'):
            return True
    return False


def wrap(status, data, request_id):
    raw = data.get('error')
    error = dict(raw) if isinstance(raw, dict) else {}
    if error.get('type') not in ERROR_TYPES.values():
        error['type'] = ERROR_TYPES.get(status, 'api_error' if status >= 500 else 'invalid_request_error')
    error.setdefault('message', 'Request failed')
    return {**data, 'type': 'error', 'error': error, 'request_id': request_id}
