from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.repositories import webhook_events as repo


class _DummyDatabase:
    def __init__(self, *, count: int = 0) -> None:
        self.count = count
        self.fetch_one_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetch_one(self, sql: str, params: tuple) -> dict[str, int]:
        self.fetch_one_calls.append((sql, params))
        return {"count": self.count}

    async def execute(self, sql: str, params: tuple) -> None:
        self.execute_calls.append((sql, params))


class _ExecuteOnlyDatabase:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, params: tuple) -> None:
        self.execute_calls.append((sql, params))


def test_delete_succeeded_before_removes_records(monkeypatch):
    dummy_db = _DummyDatabase(count=3)
    monkeypatch.setattr(repo, "db", dummy_db)

    cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
    deleted = asyncio.run(repo.delete_succeeded_before(cutoff))

    assert deleted == 3
    assert dummy_db.fetch_one_calls, "Expected count query to execute"
    params = dummy_db.fetch_one_calls[0][1]
    assert isinstance(params[0], datetime)
    assert params[0].tzinfo is None, "Cutoff should be stored as naive UTC"
    assert dummy_db.execute_calls, "Expected delete to run when count > 0"
    assert "DELETE FROM webhook_events" in dummy_db.execute_calls[0][0]


def test_delete_succeeded_before_skips_when_empty(monkeypatch):
    dummy_db = _DummyDatabase(count=0)
    monkeypatch.setattr(repo, "db", dummy_db)

    cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
    deleted = asyncio.run(repo.delete_succeeded_before(cutoff))

    assert deleted == 0
    assert dummy_db.fetch_one_calls, "Count query should still run"
    assert not dummy_db.execute_calls, "Delete should be skipped when nothing matches"


def test_delete_event_executes_delete(monkeypatch):
    dummy_db = _ExecuteOnlyDatabase()
    monkeypatch.setattr(repo, "db", dummy_db)

    asyncio.run(repo.delete_event(42))

    assert dummy_db.execute_calls == [
        ("DELETE FROM webhook_events WHERE id = %s", (42,)),
    ]


def test_delete_events_by_status_removes_matching_records(monkeypatch):
    dummy_db = _DummyDatabase(count=5)
    monkeypatch.setattr(repo, "db", dummy_db)

    deleted = asyncio.run(repo.delete_events_by_status("FAILED"))

    assert deleted == 5
    assert dummy_db.fetch_one_calls == [
        ("SELECT COUNT(*) AS count FROM webhook_events WHERE status = %s", ("failed",)),
    ]
    assert dummy_db.execute_calls == [
        ("DELETE FROM webhook_events WHERE status = %s", ("failed",)),
    ]


def test_delete_events_by_status_handles_empty_set(monkeypatch):
    dummy_db = _DummyDatabase(count=0)
    monkeypatch.setattr(repo, "db", dummy_db)

    deleted = asyncio.run(repo.delete_events_by_status("succeeded"))

    assert deleted == 0
    assert dummy_db.fetch_one_calls == [
        ("SELECT COUNT(*) AS count FROM webhook_events WHERE status = %s", ("succeeded",)),
    ]
    assert dummy_db.execute_calls == []


def test_delete_events_by_status_rejects_unsupported_status(monkeypatch):
    dummy_db = _DummyDatabase(count=3)
    monkeypatch.setattr(repo, "db", dummy_db)

    try:
        asyncio.run(repo.delete_events_by_status("pending"))
    except ValueError as error:
        assert str(error) == "Unsupported status for bulk webhook deletion"
    else:
        raise AssertionError("Expected ValueError for unsupported status")
