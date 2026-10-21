"""Backpressure contracts for scheduled singleton work."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.database import DatabasePoolTimeout, DatabaseQueryTimeout
from app.services import singleton_jobs


@pytest.mark.parametrize("timeout_type", [DatabasePoolTimeout, DatabaseQueryTimeout])
def test_singleton_run_defers_database_timeouts_until_next_invocation(monkeypatch, timeout_type):
    """Saturation must not start duplicate work; the next tick may retry."""
    lease = AsyncMock(side_effect=[timeout_type("database saturated"), True])
    monkeypatch.setattr(singleton_jobs, "acquire", lease)
    calls = []

    @singleton_jobs.singleton_run("busy", ttl_seconds=60)
    async def job(value):
        calls.append(value)
        return value

    async def run():
        assert await job("deferred") is None
        assert calls == []
        assert await job("recovered") == "recovered"

    asyncio.run(run())
    assert calls == ["recovered"]
    assert lease.await_count == 2


def test_singleton_run_preserves_fallback_for_other_backend_failures(monkeypatch):
    """The established fallback still covers unavailable lease schemas."""
    monkeypatch.setattr(singleton_jobs, "acquire", AsyncMock(side_effect=RuntimeError("table unavailable")))
    calls = []

    @singleton_jobs.singleton_run("fallback")
    async def job():
        calls.append("ran")
        return "result"

    assert asyncio.run(job()) == "result"
    assert calls == ["ran"]


def test_singleton_run_does_not_swallow_job_database_timeout(monkeypatch):
    """Only lease acquisition is deferred; task failures retain their signal."""
    monkeypatch.setattr(singleton_jobs, "acquire", AsyncMock(return_value=True))

    @singleton_jobs.singleton_run("job-failure")
    async def job():
        raise DatabaseQueryTimeout("job query timed out")

    with pytest.raises(DatabaseQueryTimeout, match="job query timed out"):
        asyncio.run(job())
