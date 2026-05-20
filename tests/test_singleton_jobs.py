"""Tests for the distributed singleton-job lease helper.

Uses SQLite (the project's fallback DB engine) so the tests run without
a live MySQL.  We exercise the high-level contract: acquiring a fresh
lease succeeds, a second instance is locked out until the lease
expires, and ``release`` returns ownership.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.database import db
from app.services import singleton_jobs


@pytest.fixture(autouse=True)
def _isolate_sqlite(tmp_path, monkeypatch):
    """Force SQLite and point the DB file at a clean per-test location."""

    monkeypatch.setattr(db, "_use_sqlite", True, raising=False)
    monkeypatch.setattr(
        db,
        "_get_sqlite_path",
        lambda: tmp_path / "singleton_test.db",
        raising=False,
    )

    async def _setup() -> None:
        # Disconnect any cached connection from a prior test and create
        # the singleton_jobs table directly to avoid running the full
        # migrations suite (which is MySQL-heavy and slow for this).
        try:
            await db.disconnect()
        except Exception:
            pass
        await db.connect()
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS singleton_jobs (
                job_name TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    asyncio.run(_setup())
    yield
    asyncio.run(db.disconnect())


def test_acquire_fresh_lease_succeeds():
    async def run() -> None:
        ok = await singleton_jobs.acquire("test_job", ttl_seconds=60)
        assert ok is True

    asyncio.run(run())


def test_second_instance_is_locked_out(monkeypatch):
    async def run() -> None:
        # Instance A grabs the lease.
        monkeypatch.setattr(singleton_jobs, "_INSTANCE_ID", "instance-A")
        assert await singleton_jobs.acquire("shared", ttl_seconds=60) is True

        # Instance B should not be able to steal it while still valid.
        monkeypatch.setattr(singleton_jobs, "_INSTANCE_ID", "instance-B")
        assert await singleton_jobs.acquire("shared", ttl_seconds=60) is False

    asyncio.run(run())


def test_release_returns_lease(monkeypatch):
    async def run() -> None:
        monkeypatch.setattr(singleton_jobs, "_INSTANCE_ID", "instance-A")
        await singleton_jobs.acquire("ephemeral", ttl_seconds=60)
        await singleton_jobs.release("ephemeral")

        # Instance B now sees an expired lease and can take it.
        monkeypatch.setattr(singleton_jobs, "_INSTANCE_ID", "instance-B")
        assert await singleton_jobs.acquire("ephemeral", ttl_seconds=60) is True

    asyncio.run(run())


def test_singleton_run_decorator_skips_non_owner(monkeypatch):
    calls: list[str] = []

    @singleton_jobs.singleton_run("decorated", ttl_seconds=60)
    async def job() -> None:
        calls.append(singleton_jobs.instance_id())

    async def run() -> None:
        monkeypatch.setattr(singleton_jobs, "_INSTANCE_ID", "owner")
        await job()
        monkeypatch.setattr(singleton_jobs, "_INSTANCE_ID", "stranger")
        await job()

    asyncio.run(run())
    assert calls == ["owner"]
