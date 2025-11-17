"""Shared Redis-backed rate limit primitives."""
from __future__ import annotations

import secrets
import time
from typing import Tuple

from redis.asyncio import Redis


_RATE_LIMIT_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry = 0
    if oldest[2] then
        retry = window - (now - tonumber(oldest[2]))
    end
    if retry < 0 then retry = 0 end
    return {0, retry}
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window))
return {1, 0}
"""


async def acquire_slot(
    redis_client: Redis,
    *,
    key: str,
    limit: int,
    window_seconds: float,
) -> tuple[bool, float | None]:
    """Record an event in Redis and determine if it fits within the limit."""

    now = time.time()
    member = f"{now:.6f}:{secrets.token_hex(6)}"
    result: Tuple[int, float] | list[int | float] = await redis_client.eval(
        _RATE_LIMIT_LUA,
        1,
        key,
        float(now),
        float(window_seconds),
        int(limit),
        member,
    )
    allowed = bool(int(result[0]))
    retry_after_raw = float(result[1]) if len(result) > 1 else 0.0
    retry_after = retry_after_raw if retry_after_raw > 0 else None
    return allowed, retry_after
