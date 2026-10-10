"""Tenant-scoped, bounded cache of deterministic preprocessing results."""
import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from cryptography.fernet import InvalidToken
from history_state import cipher

MAX_VALUE, MAX_ENTRIES = 262144, 32
STORE = '''
redis.call('ZREMRANGEBYSCORE',KEYS[2],'-inf',ARGV[1])
redis.call('SET',KEYS[1],ARGV[3],'EX',ARGV[2])
redis.call('ZADD',KEYS[2],tonumber(ARGV[1])+tonumber(ARGV[2]),KEYS[1])
while redis.call('ZCARD',KEYS[2]) > tonumber(ARGV[4]) do
 local old=redis.call('ZPOPMIN',KEYS[2],1)
 redis.call('DEL',old[1])
end
redis.call('EXPIRE',KEYS[2],3601)
return 1
'''


class ProcessingCache:
    def __init__(self, manager):
        self.codec = cipher(manager, 'processing')
        self.redis = manager.shared_limits.client if manager.shared_limits else None
        self.store = self.redis.register_script(STORE) if self.redis else None
        self.local, self.lock = OrderedDict(), threading.Lock()

    def memo(self, owner, kind, value, factory, ttl=300):
        raw = json.dumps([owner, kind, value], sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        key = 'webcc:processing:v1:' + hashlib.sha256(raw).hexdigest()
        ttl = max(1, min(3600, int(ttl)))
        cached = None
        try:
            if self.redis:
                cached = self.redis.get(key)
            else:
                with self.lock:
                    if key in self.local:
                        until, cached = self.local.pop(key)
                        if until <= time.time():
                            cached = None
                        else:
                            self.local[key] = until, cached
            if cached:
                return json.loads(self.codec.decrypt(cached.encode(), ttl=ttl))
        except (InvalidToken, ValueError, TypeError):
            pass
        except Exception:
            # Preprocessing remains available when this optional cache is unavailable.
            pass
        result = factory()
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) <= MAX_VALUE:
            token = self.codec.encrypt(encoded).decode()
            try:
                if self.redis:
                    self.store(keys=[key, 'webcc:processing:index:v1'], args=[int(time.time()), ttl, token, MAX_ENTRIES])
                else:
                    with self.lock:
                        self.local[key] = time.time() + ttl, token
                        self.local.move_to_end(key)
                        while len(self.local) > MAX_ENTRIES:
                            self.local.popitem(last=False)
            except Exception:
                pass
        return copy.deepcopy(result)
