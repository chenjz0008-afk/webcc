"""Redis server-clock rolling windows; unavailable Redis never bypasses limits."""
import secrets
from redis import Redis

WINDOW = '''
local t=redis.call('TIME')
local now=tonumber(t[1])*1000+math.floor(tonumber(t[2])/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now-60000)
if redis.call('ZCARD',KEYS[1]) >= tonumber(ARGV[1]) then
 local first=redis.call('ZRANGE',KEYS[1],0,0,'WITHSCORES')
 return math.max(1,math.ceil((60000-now+tonumber(first[2]))/1000))
end
redis.call('ZADD',KEYS[1],now,ARGV[2])
redis.call('PEXPIRE',KEYS[1],61000)
return 0
'''


class SharedLimits:
    def __init__(self, url):
        self.client = Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2, decode_responses=True)
        self.client.ping()
        self.window = self.client.register_script(WINDOW)

    def admit(self, identity, rpm):
        return int(self.window(keys=['webcc:rpm:' + identity], args=[rpm, secrets.token_hex(16)]))
