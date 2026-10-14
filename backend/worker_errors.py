"""Classify bounded worker errors without exposing upstream diagnostics."""
import json


def no_session(status, body):
    if status != 500 or len(body) > 65536:
        return False
    try:
        error = json.loads(body).get('error')
    except (ValueError, AttributeError, UnicodeDecodeError):
        return False
    return isinstance(error, dict) and error.get('type') == 'no_cookie_available'


def proxy_connect(status, body):
    if status != 500 or len(body) > 65536:
        return False
    try:
        error = json.loads(body).get('error')
    except (ValueError, AttributeError, UnicodeDecodeError):
        return False
    return (isinstance(error, dict) and error.get('type') == 'wreq_error' and
            isinstance(error.get('message'), str) and '(ProxyConnect)' in error['message'])
