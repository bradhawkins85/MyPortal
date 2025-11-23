from typing import Any

import pytest

from app.repositories import automations


class _DummyAutomationDB:
    def __init__(
        self,
        fetched_row,
        *,
        fetched_rows: list[dict[str, Any]] | None = None,
        last_insert_id: int = 99,
        connected: bool = True,
    ):
        self.insert_sql: str | None = None
        self.insert_params: tuple | None = None
        self.fetch_sql: str | None = None
        self.fetch_params: tuple | None = None
        self.fetch_all_sql: str | None = None
        self.fetch_all_params: tuple | None = None
        self._fetched_row = fetched_row
        self._fetched_rows = fetched_rows
        self._last_insert_id = last_insert_id
        self._connected = connected
        self.connect_calls = 0

    def is_connected(self):  # pragma: no cover - exercised indirectly
        return self._connected

    async def connect(self):  # pragma: no cover - exercised indirectly
        self.connect_calls += 1
        self._connected = True

    async def execute_returning_lastrowid(self, sql, params):  # pragma: no cover - interface parity
        self.insert_sql = sql.strip()
        self.insert_params = params
        return self._last_insert_id

    async def fetch_one(self, sql, params):  # pragma: no cover - interface parity
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return self._fetched_row

    async def fetch_all(self, sql, params):  # pragma: no cover - interface parity
        self.fetch_all_sql = sql.strip()
        self.fetch_all_params = params
        return self._fetched_rows or []


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_create_automation_returns_inserted_record(monkeypatch):
    fetched = {
        "id": 99,
        "name": "Escalate stale tickets",
        "description": "Auto escalate tickets older than 3 days",
        "kind": "scheduled",
        "cadence": "daily",
        "cron_expression": None,
        "scheduled_time": None,
        "run_once": False,
        "trigger_event": None,
        "trigger_filters": None,
        "action_module": "notify",
        "action_payload": {"channel": "ops"},
        "status": "active",
        "next_run_at": None,
        "last_run_at": None,
        "last_error": None,
        "created_at": None,
        "updated_at": None,
    }
    dummy_db = _DummyAutomationDB(fetched)
    monkeypatch.setattr(automations, "db", dummy_db)

    record = await automations.create_automation(
        name="Escalate stale tickets",
        description="Auto escalate tickets older than 3 days",
        kind="scheduled",
        cadence="daily",
        cron_expression=None,
        scheduled_time=None,
        run_once=False,
        trigger_event=None,
        trigger_filters=None,
        action_module="notify",
        action_payload={"channel": "ops"},
        status="active",
        next_run_at=None,
    )

    assert record["id"] == 99
    assert dummy_db.fetch_sql == "SELECT * FROM automations WHERE id = %s"
    assert dummy_db.fetch_params == (99,)
    assert "status" in dummy_db.insert_sql
    assert "next_run_at" in dummy_db.insert_sql


@pytest.mark.anyio
async def test_create_automation_reconnects_when_pool_missing(monkeypatch):
    dummy_db = _DummyAutomationDB(fetched_row=None, last_insert_id=201, connected=False)
    monkeypatch.setattr(automations, "db", dummy_db)

    record = await automations.create_automation(
        name="Reconnect automation",
        description=None,
        kind="event",
        cadence=None,
        cron_expression=None,
        scheduled_time=None,
        run_once=False,
        trigger_event="tickets.created",
        trigger_filters=None,
        action_module="webhook",
        action_payload={"url": "https://example.com"},
        status="inactive",
        next_run_at=None,
    )

    assert record["id"] == 201
    assert dummy_db.connect_calls == 1


@pytest.mark.anyio
async def test_create_automation_falls_back_when_fetch_missing(monkeypatch):
    dummy_db = _DummyAutomationDB(fetched_row=None, last_insert_id=101)
    monkeypatch.setattr(automations, "db", dummy_db)

    record = await automations.create_automation(
        name="Auto close",
        description=None,
        kind="event",
        cadence=None,
        cron_expression=None,
        scheduled_time=None,
        run_once=False,
        trigger_event="tickets.closed",
        trigger_filters={"match": {"status": "closed"}},
        action_module="webhook",
        action_payload={"url": "https://example.com"},
        status="inactive",
        next_run_at=None,
    )

    assert record["id"] == 101
    assert record["name"] == "Auto close"
    assert record["trigger_filters"] == {"match": {"status": "closed"}}
    assert record["action_payload"] == {"url": "https://example.com"}
    assert record["status"] == "inactive"


@pytest.mark.anyio
async def test_record_run_returns_inserted_record(monkeypatch):
    fetched = {
        "id": 55,
        "automation_id": 9,
        "status": "succeeded",
        "started_at": None,
        "finished_at": None,
        "duration_ms": 1200,
        "result_payload": {"ok": True},
        "error_message": None,
    }
    dummy_db = _DummyAutomationDB(fetched_row=fetched, last_insert_id=55)
    monkeypatch.setattr(automations, "db", dummy_db)

    record = await automations.record_run(
        automation_id=9,
        status="succeeded",
        started_at=None,
        finished_at=None,
        duration_ms=1200,
        result_payload={"ok": True},
        error_message=None,
    )

    assert record["id"] == 55
    assert dummy_db.fetch_sql == "SELECT * FROM automation_runs WHERE id = %s"
    assert dummy_db.fetch_params == (55,)


@pytest.mark.anyio
async def test_record_run_falls_back_when_fetch_missing(monkeypatch):
    dummy_db = _DummyAutomationDB(fetched_row=None, last_insert_id=77)
    monkeypatch.setattr(automations, "db", dummy_db)

    record = await automations.record_run(
        automation_id=3,
        status="failed",
        started_at=None,
        finished_at=None,
        duration_ms=None,
        result_payload={"error": "timeout"},
        error_message="timeout",
    )

    assert record["id"] == 77
    assert record["automation_id"] == 3
    assert record["status"] == "failed"
    assert record["result_payload"] == {"error": "timeout"}


@pytest.mark.anyio
async def test_list_event_automations_without_limit(monkeypatch):
    rows = [
        {
            "id": 301,
            "name": "Ticket ntfy alert",
            "description": None,
            "kind": "event",
            "cadence": None,
            "cron_expression": None,
            "scheduled_time": None,
            "run_once": False,
            "trigger_event": "tickets.created",
            "trigger_filters": None,
            "action_module": "ntfy",
            "action_payload": {"message": "Created"},
            "status": "active",
            "next_run_at": None,
            "last_run_at": None,
            "last_error": None,
            "created_at": None,
            "updated_at": None,
        }
    ]
    dummy_db = _DummyAutomationDB(fetched_row=None, fetched_rows=rows)
    monkeypatch.setattr(automations, "db", dummy_db)

    records = await automations.list_event_automations("tickets.created")

    assert "LIMIT" not in (dummy_db.fetch_all_sql or "")
    assert dummy_db.fetch_all_params == ("tickets.created",)
    assert len(records) == 1
    assert records[0]["id"] == 301
