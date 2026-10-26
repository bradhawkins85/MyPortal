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


def test_llm_failure_filter_uses_exact_links_function_and_inclusive_dates(monkeypatch, tmp_path):
    from app.core.config import Settings
    from app.core.database import Database
    from app.repositories import llm_usage
    from pathlib import Path

    async def run():
        test_db = Database()
        test_db._settings = Settings(
            SESSION_SECRET="test-secret", TOTP_ENCRYPTION_KEY="A" * 64,
            DB_HOST=None, DB_USER=None, DB_PASSWORD=None, DB_NAME=None,
        )
        test_db._use_sqlite = True
        test_db._get_sqlite_path = lambda: tmp_path / "webhooks.db"
        await test_db.connect()
        try:
            migration = Path("migrations/460_llm_usage_events.sql").read_text()
            for sql in test_db._split_sql_statements(test_db._adapt_sql_for_sqlite(migration)):
                await test_db.execute(sql)
            await test_db.execute("CREATE TABLE webhook_events (id INTEGER PRIMARY KEY, name TEXT, status TEXT, updated_at TEXT)")
            monkeypatch.setattr(repo, "db", test_db)
            monkeypatch.setattr(llm_usage, "db", test_db)
            for event_id in range(1, 8):
                await test_db.execute(
                    "INSERT INTO webhook_events VALUES (?, ?, ?, ?)",
                    (event_id, "same provider and model", "succeeded" if event_id == 1 else "failed", "2026-10-03 00:00:00"),
                )
            for feature, status, occurred_at, event_id in (
                ("rag_relationships", "failed", datetime(2026, 10, 1), 1),
                ("rag_relationships", "error", datetime(2026, 10, 2, 23, 59, 59), 2),
                ("agent", "failed", datetime(2026, 10, 2), 3),
                ("rag_relationships", "succeeded", datetime(2026, 10, 2), 4),
                ("rag_relationships", "failed", datetime(2026, 9, 30, 23, 59, 59), 5),
                ("rag_relationships", "failed", datetime(2026, 10, 3), 6),
                ("rag_relationships", "failed", datetime(2026, 10, 2), 999),
                ("rag_relationships", "failed", datetime(2026, 10, 2), None),
                ("rag_relationships", "failed", datetime(2026, 10, 2), 1),
            ):
                await llm_usage.record_event(
                    occurred_at=occurred_at, feature=feature, provider="ollama", model="qwen",
                    status=status, input_tokens=0, output_tokens=0, tokens_estimated=False,
                    duration_ms=10, webhook_event_id=event_id,
                )
            rows = await repo.list_events(
                llm_feature="rag_relationships", llm_start=datetime(2026, 10, 1),
                llm_end=datetime(2026, 10, 3),
            )
            assert {row["id"] for row in rows} == {1, 2}
            assert len(rows) == 2
            assert await repo.list_events(llm_feature="rag_relationships' OR 1=1 --") == []
        finally:
            await test_db.disconnect()

    asyncio.run(run())
