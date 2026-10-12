"""RMM event logs: the separate event log database, extra logs per company and device, ingestion."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core import event_log_database
from app.core.event_log_database import EventLogDatabase
from app.repositories import rmm_event_logs as event_log_repo
from app.services import rmm_event_logs as event_logs
from tests.test_rmm_scripts import _enrol, _environment, anyio_backend, sqlite_db  # noqa: F401 - fixtures


@pytest.fixture
async def event_db(monkeypatch):
    database = EventLogDatabase(sqlite_path=":memory:")
    monkeypatch.setattr(event_log_repo, "event_log_db", database)
    monkeypatch.setattr(event_logs, "event_log_db", database)
    yield database
    await database.disconnect()


def _entry(record_id, *, log="System", level=4, message="Service started", minutes_ago=5, event_id=7036):
    return {
        "log_name": log, "record_id": record_id, "event_id": event_id, "level": level,
        "provider": "Service Control Manager", "computer": "PC-01", "message": message,
        "time_created": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    }


def test_extra_log_names_are_cleaned_and_checked():
    assert event_logs.parse_log_names(
        "Security\n\n security \nApplication\nMicrosoft-Windows-PowerShell/Operational\nWindows PowerShell\n"
    ) == ["Security", "Microsoft-Windows-PowerShell/Operational", "Windows PowerShell"]
    for bad in ("Bad;rm -rf", "../System", "-Security", "a" * 256, "Log\"quote"):
        with pytest.raises(event_logs.EventLogSettingsError):
            event_logs.parse_log_names([bad])
    with pytest.raises(event_logs.EventLogSettingsError):
        event_logs.parse_log_names([f"Log{i}" for i in range(event_logs.MAX_EXTRA_LOGS + 1)])


def test_unconfigured_database_switches_collection_off(monkeypatch):
    settings = event_log_database.get_settings()
    for name in ("event_log_database_host", "event_log_database_user", "event_log_database_name"):
        monkeypatch.setattr(settings, name, None)
    database = EventLogDatabase()
    assert not database.is_configured()
    assert database.status() == {"configured": False, "connected": False, "error": None}


@pytest.mark.anyio
async def test_off_without_the_event_log_database(sqlite_db, monkeypatch):
    database = EventLogDatabase()
    monkeypatch.setattr(database, "is_configured", lambda: False)
    monkeypatch.setattr(event_logs, "event_log_db", database)
    _result, agent = await _enrol(sqlite_db)
    config = await event_logs.agent_config(agent)
    assert config["enabled"] is False and config["logs"] == []
    assert await event_logs.ingest(agent, [_entry(1)]) is None
    assert await event_logs.prune() == 0


@pytest.mark.anyio
async def test_connect_creates_tables_and_is_repeatable(event_db):
    assert await event_db.connect() and await event_db.connect()
    rows = await event_db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table' AND name = %s", ("rmm_event_log_entries",))
    assert rows == [{"name": "rmm_event_log_entries"}]


@pytest.mark.anyio
async def test_agents_collect_defaults_plus_company_and_device_logs(sqlite_db, event_db):
    _result, agent = await _enrol(sqlite_db)
    assert (await event_logs.agent_config(agent))["logs"] == ["Application", "System"]

    await event_logs.save_extra_logs("Security\nSetup", company_id=1, user_id=9)
    await event_logs.save_extra_logs(["Microsoft-Windows-PowerShell/Operational", "security"], asset_id=10, user_id=9)
    await event_logs.save_extra_logs("Other company only", company_id=2)
    config = await event_logs.agent_config(agent)
    assert config["enabled"] is True and config["max_batch"] == event_logs.MAX_BATCH
    assert config["logs"] == ["Application", "System", "Security", "Setup", "Microsoft-Windows-PowerShell/Operational"]
    stored = await sqlite_db.fetch_one("SELECT * FROM rmm_event_log_settings WHERE scope_key = 'asset:10'")
    assert stored["asset_id"] == 10 and stored["company_id"] is None
    assert json.loads(stored["extra_logs"]) == ["Microsoft-Windows-PowerShell/Operational", "security"]
    assert await event_log_repo.list_asset_extra_logs([10, 11]) == {10: ["Microsoft-Windows-PowerShell/Operational", "security"]}

    # Clearing the list removes the row.
    await event_logs.save_extra_logs("", company_id=1)
    assert await event_log_repo.get_extra_logs(company_id=1) == []
    assert "Setup" not in (await event_logs.agent_config(agent))["logs"]


@pytest.mark.anyio
async def test_ingest_stores_once_and_skips_logs_nobody_chose(sqlite_db, event_db):
    _result, agent = await _enrol(sqlite_db)
    await event_logs.save_extra_logs("Security", asset_id=10)
    batch = [
        _entry(1),
        _entry(2, level=2, message="Disk error on <b>C:</b>", event_id=7),
        _entry(2),  # a repeat in the same batch
        _entry(5, log="security", level=0, message="Logon"),
        _entry(9, log="Microsoft-Windows-Sysmon/Operational"),
        _entry(3, log="Application", level=1, message="x" * 40_000, minutes_ago=60 * 24 * 40),
    ]
    assert await event_logs.ingest(agent, batch) == {"received": 6, "stored": 4, "skipped": 1, "dropped": 0, "runs": 0, "tickets": 0}
    assert (await event_logs.ingest(agent, batch[:2]))["stored"] == 0

    entries = await event_log_repo.list_entries(company_id=1)
    assert sorted(entry["record_id"] for entry in entries) == [1, 2, 3, 5]
    assert {entry["log_name"] for entry in entries} == {"System", "Security", "Application"}
    assert all(entry["asset_id"] == 10 and entry["agent_id"] == agent["id"] for entry in entries)
    long_message = next(entry for entry in entries if entry["record_id"] == 3)["message"]
    assert len(long_message) == event_logs.MAX_MESSAGE_LENGTH

    errors = await event_log_repo.list_entries(company_id=1, max_level=2)
    assert sorted(entry["record_id"] for entry in errors) == [2, 3]
    assert [e["record_id"] for e in await event_log_repo.list_entries(company_id=1, search="<b>C:")] == [2]
    assert [e["record_id"] for e in await event_log_repo.list_entries(company_id=1, search="%")] == []
    assert [e["record_id"] for e in await event_log_repo.list_entries(company_id=1, log_name="Security")] == [5]
    assert [e["record_id"] for e in await event_log_repo.list_entries(company_id=1, event_id=7)] == [2]
    assert await event_log_repo.list_entries(company_id=2) == []
    assert await event_log_repo.list_entries(company_id=1, asset_id=11) == []

    # Newest first.
    newest = await event_log_repo.list_entries(company_id=1, limit=2)
    assert newest[0]["time_created"] >= newest[1]["time_created"]

    # Entries older than EVENT_LOG_RETENTION_DAYS (30 by default) are pruned.
    assert await event_logs.prune() == 1
    assert sorted(e["record_id"] for e in await event_log_repo.list_entries(company_id=1)) == [1, 2, 5]


@pytest.mark.anyio
async def test_agent_routes_validate_and_report_collection_off(sqlite_db, event_db, monkeypatch):
    from pydantic import ValidationError

    from app.features.rmm import event_log_routes as routes

    _result, agent = await _enrol(sqlite_db)
    batch = routes.AgentEventLogBatch(entries=[{**_entry(1), "time_created": "2026-10-11T10:00:00.1234567Z"}])
    assert await routes.agent_event_logs(batch, agent) == {"received": 1, "stored": 1, "skipped": 0, "dropped": 0, "runs": 0, "tickets": 0}
    with pytest.raises(ValidationError):
        routes.AgentEventLogBatch(entries=[_entry(i) for i in range(event_logs.MAX_BATCH + 1)])

    monkeypatch.setattr(event_db, "is_configured", lambda: False)
    await event_db.disconnect()
    response = await routes.agent_event_logs(batch, agent)
    assert response.status_code == 503
    assert (await routes.agent_event_log_config(agent))["enabled"] is False


def test_event_logs_page_renders_and_escapes():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    html = env.get_template("rmm/event_logs.html").render(
        company={"name": "Contoso"},
        database={"configured": True, "connected": True, "error": None},
        default_logs=["Application", "System"],
        company_logs=["Security"],
        devices=[{"asset_id": 10, "asset_name": "<i>PC-01</i>", "hostname": "pc", "os": "windows",
                  "extra_logs": ["Microsoft-Windows-PowerShell/Operational"]}],
        log_choices=["Application", "System", "Security"],
        level_filters=[("", "Any level"), ("2", "Error or worse")],
        entries=[{"asset_id": 10, "log_name": "System", "level_label": "Error", "provider": "Disk",
                  "event_id": 7, "message": "<script>bad</script>\nsecond line", "time_created": "2026-10-11T10:00:00",
                  "computer": "PC-01"}],
        filters={"asset_id": 10, "log": "System", "level": "2", "event_id": "", "q": "", "limit": 200},
        can_edit=True,
        retention_days=30,
        csrf_token="tok",
    )
    assert "<script>bad</script>" not in html and "&lt;script&gt;bad&lt;/script&gt;" in html
    assert "&lt;i&gt;PC-01&lt;/i&gt;" in html
    assert 'action="/rmm/event-logs/assets/10"' in html and "Save company logs" in html
    assert "Microsoft-Windows-PowerShell/Operational</textarea>" in html
    assert "kept for 30 days" in html
    assert 'name="source"' in html and 'name="from" type="date"' in html and 'name="to" type="date"' in html

    off = env.get_template("rmm/event_logs.html").render(
        company=None, database={"configured": False, "connected": False, "error": None}, default_logs=["Application", "System"],
        company_logs=[], devices=[], log_choices=[], level_filters=[], entries=[], can_edit=False, retention_days=0,
        filters={"asset_id": None, "log": "", "level": "", "event_id": "", "q": "", "limit": 200},
    )
    assert "Event log collection is off" in off and "Save company logs" not in off
