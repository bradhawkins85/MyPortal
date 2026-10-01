"""Schedule dispatcher; this module never imports or performs SIP operations."""
from __future__ import annotations

from app.repositories import voice_monitor
from app.services.redis import get_redis_client


class VoiceMonitorDispatcher:
    """Identify due endpoints and create unique durable attempts."""

    async def dispatch(self, *, limit: int = 100) -> int:
        count = await voice_monitor.enqueue_due_attempts(limit=limit)
        redis = get_redis_client()
        if count and redis is not None:
            # Redis is only a low-latency wake-up hint. The database remains the
            # durable queue, so a lost hint cannot lose work.
            await redis.lpush("myportal:voice-monitor:wakeup", str(count))
            await redis.expire("myportal:voice-monitor:wakeup", 60)
        return count
