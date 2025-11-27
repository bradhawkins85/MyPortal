"""Redis client utilities."""
from __future__ import annotations

from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool

from app.core.config import get_settings
from app.core.logging import log_warning


__all__ = ["get_redis_client", "close_redis_client"]


_redis_pool: ConnectionPool | None = None
_redis_client: Redis | None = None


def get_redis_client() -> Redis | None:
    """Return a shared Redis client when a connection URL is configured."""

    global _redis_client, _redis_pool
    if _redis_client is not None:
        return _redis_client

    settings = get_settings()
    redis_url = settings.redis_url
    if not redis_url:
        return None

    try:
        _redis_pool = ConnectionPool.from_url(redis_url, decode_responses=True)
        _redis_client = Redis(connection_pool=_redis_pool)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_warning("Unable to configure Redis client", error=str(exc))
        _redis_pool = None
        _redis_client = None
        return None

    return _redis_client


async def close_redis_client() -> None:
    """Close the shared Redis client and release the connection pool."""

    global _redis_client, _redis_pool
    client = _redis_client
    pool = _redis_pool
    _redis_client = None
    _redis_pool = None

    if client is not None:
        try:
            await client.aclose()
        except Exception:  # pragma: no cover - defensive cleanup
            pass

    if pool is not None:
        try:
            await pool.disconnect()
        except Exception:  # pragma: no cover - defensive cleanup
            pass
