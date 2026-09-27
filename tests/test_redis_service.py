from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.services import redis as redis_service


def test_close_redis_client_suppresses_cleanup_errors(monkeypatch):
    client = AsyncMock()
    client.aclose.side_effect = RuntimeError("Redis is already unavailable")
    pool = AsyncMock()

    monkeypatch.setattr(redis_service, "_redis_client", client)
    monkeypatch.setattr(redis_service, "_redis_pool", pool)

    asyncio.run(redis_service.close_redis_client())

    client.aclose.assert_awaited_once_with()
    pool.disconnect.assert_awaited_once_with()
    assert redis_service._redis_client is None
    assert redis_service._redis_pool is None
