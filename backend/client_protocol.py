"""Public API browser access and Anthropic model catalogue framing."""
from datetime import datetime, timezone
from urllib.parse import urlsplit


HEADERS = {'authorization', 'x-api-key', 'content-type', 'accept', 'anthropic-version',
           'anthropic-beta', 'anthropic-dangerous-direct-browser-access',
           'x-stainless-lang', 'x-stainless-package-version', 'x-stainless-os',
           'x-stainless-arch', 'x-stainless-runtime', 'x-stainless-runtime-version',
           'x-stainless-retry-count', 'x-stainless-timeout', 'x-webcc-tools',
           'x-webcc-model-policy'}


def public(path):
    target = urlsplit(path)
    return not target.scheme and not target.netloc and target.path.startswith(('/v1/', '/code/v1/'))


def catalogue(data):
    result = {**data, 'has_more': data.get('has_more', False)}
    models = []
    for item in data['data']:
        stamp = item.get('created', 0)
        stamp = stamp if isinstance(stamp, (int, float)) and 0 <= stamp <= 253402300799 else 0
        models.append({**item, 'type': 'model', 'lifecycle': item.get('lifecycle', 'active'), 'display_name': item.get('display_name') or item['id'],
                       'created_at': item.get('created_at') or datetime.fromtimestamp(stamp, timezone.utc).isoformat()})
    result.update(data=models, first_id=models[0]['id'] if models else None,
                  last_id=models[-1]['id'] if models else None)
    return result
