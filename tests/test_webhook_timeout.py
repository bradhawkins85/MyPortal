from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.repositories import webhook_events as repo
from app.services import webhook_monitor


class _DummyDatabase:
    def __init__(self, *, events: list[dict] = None) -> None:
        self.events = events or []
        self.fetch_all_calls: list[tuple[str, tuple]] = []
        self.fetch_one_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetch_all(self, sql: str, params: tuple) -> list[dict]:
        self.fetch_all_calls.append((sql, params))
        return self.events

    async def fetch_one(self, sql: str, params: tuple) -> dict[str, int] | None:
        self.fetch_one_calls.append((sql, params))
        return None

    async def execute(self, sql: str, params: tuple) -> None:
        self.execute_calls.append((sql, params))


def test_list_stalled_events_finds_old_in_progress_events(monkeypatch):
    old_event = {
        "id": 1,
        "name": "test",
        "status": "in_progress",
        "updated_at": datetime(2025, 1, 1, 12, 0, 0),
        "attempt_count": 0,
        "max_attempts": 3,
        "backoff_seconds": 300,
        "headers": None,
        "payload": None,
        "created_at": datetime(2025, 1, 1, 12, 0, 0),
        "next_attempt_at": None,
    }
    dummy_db = _DummyDatabase(events=[old_event])
    monkeypatch.setattr(repo, "db", dummy_db)

    events = asyncio.run(repo.list_stalled_events(timeout_seconds=600))

    assert len(events) == 1
    assert events[0]["id"] == 1
    assert events[0]["status"] == "in_progress"
    assert dummy_db.fetch_all_calls
    sql = dummy_db.fetch_all_calls[0][0]
    assert "status = 'in_progress'" in sql
    assert "updated_at <" in sql


def test_list_stalled_events_returns_empty_when_no_stalled(monkeypatch):
    dummy_db = _DummyDatabase(events=[])
    monkeypatch.setattr(repo, "db", dummy_db)

    events = asyncio.run(repo.list_stalled_events(timeout_seconds=600))

    assert len(events) == 0
    assert dummy_db.fetch_all_calls


def test_fail_stalled_events_marks_events_as_failed(monkeypatch):
    stalled_event = {
        "id": 42,
        "name": "stalled-webhook",
        "status": "in_progress",
        "updated_at": datetime(2025, 1, 1, 12, 0, 0),
        "attempt_count": 1,
        "max_attempts": 3,
        "backoff_seconds": 300,
        "headers": None,
        "payload": None,
        "created_at": datetime(2025, 1, 1, 12, 0, 0),
        "next_attempt_at": None,
    }

    list_calls = []
    record_calls = []
    mark_failed_calls = []

    async def fake_list_stalled(timeout_seconds: int):
        list_calls.append(timeout_seconds)
        return [stalled_event]

    async def fake_record_attempt(**kwargs):
        record_calls.append(kwargs)

    async def fake_mark_event_failed(**kwargs):
        mark_failed_calls.append(kwargs)

    monkeypatch.setattr(webhook_monitor.webhook_repo, "list_stalled_events", fake_list_stalled)
    monkeypatch.setattr(webhook_monitor.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(webhook_monitor.webhook_repo, "mark_event_failed", fake_mark_event_failed)

    log_error_calls = []
    log_info_calls = []

    def fake_log_error(message: str, **context) -> None:
        log_error_calls.append((message, context))

    def fake_log_info(message: str, **context) -> None:
        log_info_calls.append((message, context))

    monkeypatch.setattr(webhook_monitor, "log_error", fake_log_error)
    monkeypatch.setattr(webhook_monitor, "log_info", fake_log_info)

    count = asyncio.run(webhook_monitor.fail_stalled_events(timeout_seconds=600))

    assert count == 1
    assert list_calls == [600]
    assert len(record_calls) == 1
    assert record_calls[0]["event_id"] == 42
    assert record_calls[0]["attempt_number"] == 2
    assert record_calls[0]["status"] == "timeout"
    assert "timed out" in record_calls[0]["error_message"]
    assert len(mark_failed_calls) == 1
    assert mark_failed_calls[0]["event_id"] == 42
    assert mark_failed_calls[0]["attempt_number"] == 2
    assert "timed out" in mark_failed_calls[0]["error_message"]
    assert any("Webhook event timed out" in msg for msg, _ in log_error_calls)
    assert any("Failed stalled webhook events" in msg for msg, _ in log_info_calls)


def test_fail_stalled_events_returns_zero_when_none_stalled(monkeypatch):
    async def fake_list_stalled(timeout_seconds: int):
        return []

    monkeypatch.setattr(webhook_monitor.webhook_repo, "list_stalled_events", fake_list_stalled)

    count = asyncio.run(webhook_monitor.fail_stalled_events(timeout_seconds=600))

    assert count == 0


def test_fail_stalled_events_handles_multiple_events(monkeypatch):
    events = [
        {
            "id": 1,
            "name": "event1",
            "status": "in_progress",
            "attempt_count": 0,
            "max_attempts": 3,
            "backoff_seconds": 300,
        },
        {
            "id": 2,
            "name": "event2",
            "status": "in_progress",
            "attempt_count": 1,
            "max_attempts": 3,
            "backoff_seconds": 300,
        },
    ]

    async def fake_list_stalled(timeout_seconds: int):
        return events

    record_calls = []
    mark_failed_calls = []

    async def fake_record_attempt(**kwargs):
        record_calls.append(kwargs)

    async def fake_mark_event_failed(**kwargs):
        mark_failed_calls.append(kwargs)

    monkeypatch.setattr(webhook_monitor.webhook_repo, "list_stalled_events", fake_list_stalled)
    monkeypatch.setattr(webhook_monitor.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(webhook_monitor.webhook_repo, "mark_event_failed", fake_mark_event_failed)
    monkeypatch.setattr(webhook_monitor, "log_error", lambda *args, **kwargs: None)
    monkeypatch.setattr(webhook_monitor, "log_info", lambda *args, **kwargs: None)

    count = asyncio.run(webhook_monitor.fail_stalled_events(timeout_seconds=600))

    assert count == 2
    assert len(record_calls) == 2
    assert len(mark_failed_calls) == 2
    assert record_calls[0]["event_id"] == 1
    assert record_calls[1]["event_id"] == 2
