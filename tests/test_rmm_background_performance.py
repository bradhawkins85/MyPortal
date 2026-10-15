"""Database work stays bounded as the RMM fleet and run history grow."""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.repositories import rmm, rmm_checks as repo
from app.services import rmm_checks as checks


class MemoryDB:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.queries = []

    async def fetch_all(self, sql, params=()):
        self.queries.append((sql, params))
        return [dict(row) for row in self.connection.execute(sql.replace("%s", "?"), params)]

    async def execute_rowcount(self, sql, params=()):
        self.queries.append((sql, params))
        values = tuple(value.isoformat(" ") if isinstance(value, datetime) else value for value in params)
        return self.connection.execute(sql.replace("%s", "?"), values).rowcount


@pytest.fixture
def database(monkeypatch):
    database = MemoryDB()
    monkeypatch.setattr(repo, "db", database)
    monkeypatch.setattr(rmm, "db", database)
    yield database
    database.connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["offline", "script"])
async def test_background_checks_batch_state_for_large_fleet(database, monkeypatch, kind):
    now = datetime(2026, 10, 15, 12)
    database.connection.executescript("""
        CREATE TABLE rmm_check_states (
            check_id INTEGER, agent_id INTEGER, status TEXT, value_text TEXT,
            problem_since TEXT, checked_at TEXT, changed_at TEXT, last_action_at TEXT,
            ticket_id INTEGER, run_id INTEGER, next_run_at TEXT, scheduled_at TEXT
        );
    """)
    database.connection.executemany(
        "INSERT INTO rmm_check_states VALUES (1, ?, 'passing', 'Checked in just now', "
        "NULL, ?, ?, NULL, NULL, NULL, ?, NULL)",
        [(agent_id, str(now), str(now), str(now + timedelta(hours=1))) for agent_id in range(1, 1001)],
    )
    agents = [{"id": number, "company_id": 1, "asset_id": number, "last_seen_utc": now}
              for number in range(1, 1001)]
    check = {"id": 1, "check_type": kind, "target": "", "company_id": None,
             "asset_id": None, "is_enabled": True, "duration_minutes": 30}
    monkeypatch.setattr(repo, "list_checks_of_types", AsyncMock(return_value=[check]))
    monkeypatch.setattr(repo, "list_active_agents", AsyncMock(return_value=agents))
    single_read = AsyncMock(side_effect=AssertionError("Unexpected per-device state read"))
    save = AsyncMock()
    monkeypatch.setattr(repo, "list_states_for_agent", single_read)
    monkeypatch.setattr(repo, "get_state", single_read)
    monkeypatch.setattr(repo, "save_state", save)
    if kind == "offline":
        assert await checks.evaluate_offline(now) == 1000
    else:
        assert await checks.run_due_script_checks(now) == 0
    assert len(database.queries) == 3
    assert max(len(params) for _, params in database.queries) == 400
    single_read.assert_not_awaited()
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_offline_result_uses_known_missing_state(monkeypatch):
    now = datetime(2026, 10, 15, 12)
    agent = {"id": 1, "company_id": 1, "asset_id": 10, "last_seen_utc": now}
    check = {"id": 2, "check_type": "offline", "target": "", "company_id": None,
             "asset_id": None, "is_enabled": True, "duration_minutes": 30}
    monkeypatch.setattr(repo, "list_checks_of_types", AsyncMock(return_value=[check]))
    monkeypatch.setattr(repo, "list_active_agents", AsyncMock(return_value=[agent]))
    monkeypatch.setattr(repo, "list_states_for_agents", AsyncMock(return_value={1: {}}))
    single_read = AsyncMock(side_effect=AssertionError("State already known to be missing"))
    save = AsyncMock()
    monkeypatch.setattr(repo, "get_state", single_read)
    monkeypatch.setattr(repo, "save_state", save)
    assert await checks.evaluate_offline(now) == 1
    single_read.assert_not_awaited()
    save.assert_awaited_once()
    assert save.call_args.args[:2] == (2, 1)
    assert save.call_args.args[2]["status"] == "passing"


@pytest.mark.asyncio
async def test_bulk_state_keeps_missing_agents_and_normalises_dates(database):
    database.connection.executescript("""
        CREATE TABLE rmm_check_states (
            check_id INTEGER, agent_id INTEGER, status TEXT, value_text TEXT,
            problem_since TEXT, checked_at TEXT, changed_at TEXT, last_action_at TEXT,
            ticket_id INTEGER, run_id INTEGER, next_run_at TEXT, scheduled_at TEXT
        );
        INSERT INTO rmm_check_states VALUES
            (1, 2, 'failing', 'Offline', '2026-10-15 12:00:00', NULL, NULL, NULL, 99, NULL, NULL, NULL),
            (2, 2, 'passing', 'OK', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL),
            (1, 3, 'passing', 'Other device', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL);
    """)
    states = await repo.list_states_for_agents([1, 2, 2])
    assert states[1] == {}
    assert set(states[2]) == {1, 2}
    assert states[2][1]["problem_since"] == datetime(2026, 10, 15, 12)
    assert states[2][1]["ticket_id"] == 99
    assert 3 not in states
    assert len(database.queries) == 1
    assert await repo.list_states_for_agents([]) == {}
    assert len(database.queries) == 1


@pytest.mark.asyncio
async def test_expiry_is_one_update_and_preserves_live_runs(database):
    now = datetime(2026, 10, 15, 12)
    database.connection.execute(
        "CREATE TABLE rmm_script_runs (id INTEGER, status TEXT, expires_at TEXT, "
        "error_message TEXT, completed_at TEXT, payload_encrypted TEXT)"
    )
    rows = [(number, status, now - timedelta(minutes=1))
            for number, status in enumerate(["queued", "dispatched", "running"] * 200, 1)]
    rows += [(601, "queued", now + timedelta(minutes=1)), (602, "completed", now - timedelta(minutes=1)),
             (603, "cancelled", now - timedelta(minutes=1)), (604, "running", None), (605, "queued", now)]
    database.connection.executemany(
        "INSERT INTO rmm_script_runs VALUES (?, ?, ?, NULL, NULL, 'secret')",
        [(number, status, str(expiry) if expiry is not None else None) for number, status, expiry in rows],
    )
    assert await rmm.expire_stale_runs(now) == 600
    assert len(database.queries) == 1
    result = {row["id"]: dict(row) for row in database.connection.execute("SELECT * FROM rmm_script_runs")}
    assert result[1]["status"] == "expired"
    assert "did not collect" in result[1]["error_message"]
    for number in (2, 3):
        assert result[number]["status"] == "timed_out"
        assert "did not report" in result[number]["error_message"]
    assert all(result[number]["payload_encrypted"] is None for number in range(1, 601))
    assert all(result[number]["completed_at"] == str(now) for number in range(1, 601))
    assert all(result[number]["payload_encrypted"] == "secret" for number in range(601, 606))
    assert await rmm.expire_stale_runs(now) == 0
