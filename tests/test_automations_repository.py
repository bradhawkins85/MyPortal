import pytest

from app.repositories import automations


class _DummyAutomationDB:
    def __init__(self, fetched_row, *, last_insert_id: int = 99):
        self.insert_sql: str | None = None
        self.insert_params: tuple | None = None
        self.fetch_sql: str | None = None
        self.fetch_params: tuple | None = None
        self._fetched_row = fetched_row
        self._last_insert_id = last_insert_id

    async def execute_returning_lastrowid(self, sql, params):  # pragma: no cover - interface parity
        self.insert_sql = sql.strip()
        self.insert_params = params
        return self._last_insert_id

    async def fetch_one(self, sql, params):  # pragma: no cover - interface parity
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return self._fetched_row


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
