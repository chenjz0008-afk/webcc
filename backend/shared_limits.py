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

QUEUE = '''
local t=redis.call('TIME')
local now=tonumber(t[1])*1000+math.floor(tonumber(t[2])/1000)
local expired=redis.call('ZRANGEBYSCORE',KEYS[2],'-inf',now)
for _,ticket in ipairs(expired) do
 redis.call('ZREM',KEYS[1],ticket)
 redis.call('ZREM',KEYS[2],ticket)
 redis.call('ZREM',KEYS[4],ticket)
end
if ARGV[1]=='join' then
 if redis.call('ZCARD',KEYS[1])>=tonumber(ARGV[3]) then return 0 end
 redis.call('ZADD',KEYS[1],redis.call('INCR',KEYS[3]),ARGV[2])
 redis.call('ZADD',KEYS[2],now+tonumber(ARGV[4]),ARGV[2])
 return 1
elseif ARGV[1]=='head' then
 local score=redis.call('ZSCORE',KEYS[1],ARGV[2])
 if not score then return 0 end
 redis.call('ZADD',KEYS[4],score,ARGV[2])
 return redis.call('ZRANK',KEYS[4],ARGV[2])==0 and 1 or 0
elseif ARGV[1]=='pause' then
 redis.call('ZREM',KEYS[4],ARGV[2])
 return 1
end
redis.call('ZREM',KEYS[1],ARGV[2])
redis.call('ZREM',KEYS[2],ARGV[2])
redis.call('ZREM',KEYS[4],ARGV[2])
return 1
'''


class SharedLimits:
    def __init__(self, url):
        self.client = Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2, decode_responses=True)
        self.client.ping()
        self.window = self.client.register_script(WINDOW)
        self.queue = self.client.register_script(QUEUE)

    def admit(self, identity, rpm):
        return int(self.window(keys=['webcc:rpm:' + identity], args=[rpm, secrets.token_hex(16)]))

    def queue_operation(self, action, ticket, background=False, size=16, seconds=15):
        prefix = 'webcc:queue:{' + ('background' if background else 'interactive') + '}'
        return bool(self.queue(keys=[prefix, prefix + ':expires', prefix + ':sequence', prefix + ':eligible'],
                               args=[action, ticket, size, max(1, int(seconds * 1000))]))
