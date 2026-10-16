"""One Redis-backed paced rate limit for every real SMS submission."""

import time

import redis

from ..core.config import settings


class RateLimiterUnavailable(RuntimeError):
    pass


_PACED_RATE_LUA = """
local now = redis.call('TIME')
local now_ms = (tonumber(now[1]) * 1000) + math.floor(tonumber(now[2]) / 1000)
local interval_ms = 1000 / tonumber(ARGV[1])
local next_ms = tonumber(redis.call('GET', KEYS[1])) or now_ms
local slot_ms = math.max(now_ms, next_ms)
local following_ms = slot_ms + interval_ms
redis.call('SET', KEYS[1], following_ms, 'PX', math.ceil(following_ms - now_ms + 2000))
return math.max(0, math.ceil(slot_ms - now_ms))
"""


class RedisSmsRateLimiter:
    def __init__(self, client=None, limit: int | None = None, sleeper=time.sleep):
        self.client = client or redis.Redis.from_url(
            settings['redis_url'], socket_connect_timeout=2, socket_timeout=2,
            decode_responses=True,
        )
        self.limit = limit or settings['sms_submission']['per_second']
        self.sleeper = sleeper

    def acquire(self) -> None:
        try:
            wait_ms = self.client.eval(
                _PACED_RATE_LUA, 1, 'listingiq:sms:next-submission', self.limit,
            )
        except redis.RedisError as error:
            # A live send may not silently bypass the global safety limit.
            raise RateLimiterUnavailable('The SMS rate limiter is unavailable.') from error
        if int(wait_ms) > 0:
            self.sleeper(max(0.001, int(wait_ms) / 1000.0))


_limiter = None


def acquire_submission_slot() -> None:
    global _limiter
    if _limiter is None:
        _limiter = RedisSmsRateLimiter()
    _limiter.acquire()
