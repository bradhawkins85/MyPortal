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
    assert await event_logs.ingest(agent, batch) == {"received": 6, "stored": 4, "skipped": 1, "dropped": 0, "runs": 0, "scheduled": 0, "tickets": 0, "repeats": 0, "below_level": 0}
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
    assert await routes.agent_event_logs(batch, agent) == {"received": 1, "stored": 1, "skipped": 0, "dropped": 0, "runs": 0, "scheduled": 0, "tickets": 0, "repeats": 0, "below_level": 0}
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


def test_default_logs_come_from_the_environment(monkeypatch):
    settings = event_logs.get_settings()
    monkeypatch.setattr(settings, "event_log_default_logs", " Application ; System,Security;security;;Bad\"quote;-Leading;../x ")
    assert event_logs.default_logs() == ["Application", "System", "Security"]
    # Extra logs never repeat a default.
    assert event_logs.parse_log_names("Security\nSetup") == ["Setup"]

    for empty in ("", "   ", ";;", "../bad"):
        monkeypatch.setattr(settings, "event_log_default_logs", empty)
        assert event_logs.default_logs() == ["Application", "System"]

    monkeypatch.setattr(settings, "event_log_default_logs", ";".join(f"Log{i}" for i in range(40)))
    assert len(event_logs.default_logs()) == event_logs.MAX_EXTRA_LOGS


@pytest.mark.anyio
async def test_agents_collect_the_configured_default_logs(sqlite_db, event_db, monkeypatch):
    monkeypatch.setattr(event_logs.get_settings(), "event_log_default_logs", "System;Security")
    _result, agent = await _enrol(sqlite_db)
    await event_logs.save_extra_logs("Application\nsecurity", company_id=1)
    assert (await event_logs.agent_config(agent))["logs"] == ["System", "Security", "Application"]


# --------------------------------------------------------------------------- #
# Deflating storage: shared messages, repeats and the least severe level
# --------------------------------------------------------------------------- #


def _at(record_id, minutes, *, base, **kwargs):
    return {**_entry(record_id, **kwargs), "time_created": base + timedelta(minutes=minutes)}


@pytest.mark.anyio
async def test_each_message_is_stored_once_for_every_device(sqlite_db, event_db):
    _result, first = await _enrol(sqlite_db)
    _result, second = await _enrol(sqlite_db, tray_id=6, asset_id=11, uid="agent-uid-0002")
    await event_logs.ingest(first, [_entry(1), _entry(2, event_id=7, message="Disk error")])
    await event_logs.ingest(second, [_entry(1), _entry(2, event_id=7, message="Disk error")])

    messages = await event_db.fetch_all("SELECT message FROM rmm_event_log_messages ORDER BY message")
    assert [row["message"] for row in messages] == ["Disk error", "Service started"]
    stored = await event_db.fetch_all("SELECT message, message_id FROM rmm_event_log_entries")
    assert len(stored) == 4 and all(row["message"] is None and row["message_id"] for row in stored)
    # Reading, searching and rules see the text as before.
    entries = await event_log_repo.list_entries(company_id=1)
    assert sorted(entry["message"] for entry in entries) == ["Disk error", "Disk error", "Service started", "Service started"]
    assert {e["asset_id"] for e in await event_log_repo.list_entries(company_id=1, search="disk")} == {10, 11}
    scanned = await event_log_repo.scan_entries(company_id=1, asset_id=None, event_ids=[7], after_id=0, limit=10)
    assert [row["message"] for row in scanned] == ["Disk error", "Disk error"]


@pytest.mark.anyio
async def test_repeats_of_an_event_are_one_entry_with_a_count(sqlite_db, event_db):
    _result, agent = await _enrol(sqlite_db)
    base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)
    batch = [_at(1, 0, base=base), _at(2, 1, base=base), _at(3, 2, base=base, message="Other"), _at(4, 3, base=base)]
    result = await event_logs.ingest(agent, batch)
    assert (result["stored"], result["repeats"]) == (4, 2)
    entries = {e["record_id"]: e for e in await event_log_repo.list_entries(company_id=1)}
    assert set(entries) == {1, 3}
    assert entries[1]["repeat_count"] == 3 and entries[1]["last_time_created"].startswith(str(base + timedelta(minutes=3))[:16])
    assert entries[3]["repeat_count"] == 1 and entries[3]["last_time_created"] is None

    # The next batch carries on the same entry, within the window of its first occurrence.
    result = await event_logs.ingest(agent, [_at(5, 30, base=base), _at(6, 59, base=base)])
    assert (result["stored"], result["repeats"]) == (2, 2)
    # Resending batches (the reply was lost) counts nothing twice, and new records still count.
    assert (await event_logs.ingest(agent, batch))["stored"] == 0
    result = await event_logs.ingest(agent, [_at(5, 30, base=base), _at(6, 59, base=base), _at(7, 60, base=base)])
    assert (result["stored"], result["repeats"]) == (1, 1)
    entries = {e["record_id"]: e for e in await event_log_repo.list_entries(company_id=1)}
    assert set(entries) == {1, 3} and entries[1]["repeat_count"] == 6

    # Past the window a new entry starts; a different level, source or message never joins.
    result = await event_logs.ingest(agent, [_at(8, 61, base=base), _at(9, 62, base=base, level=3)])
    assert (result["stored"], result["repeats"]) == (2, 0)
    assert sorted(e["record_id"] for e in await event_log_repo.list_entries(company_id=1)) == [1, 3, 8, 9]

    # Findings count every occurrence (all nine share the log, source and event ID).
    from app.repositories import rmm_findings as findings_repo
    original = findings_repo.event_log_db
    findings_repo.event_log_db = event_db
    try:
        counts = await findings_repo.event_counts(since=base - timedelta(hours=1), until=base + timedelta(hours=3))
    finally:
        findings_repo.event_log_db = original
    assert [row["total"] for row in counts] == [9]


@pytest.mark.anyio
async def test_a_zero_window_stores_every_occurrence(sqlite_db, event_db, monkeypatch):
    monkeypatch.setattr(event_logs.get_settings(), "event_log_repeat_window_minutes", 0)
    _result, agent = await _enrol(sqlite_db)
    assert (await event_logs.ingest(agent, [_entry(1), _entry(2), _entry(3)]))["stored"] == 3
    assert [e["repeat_count"] for e in await event_log_repo.list_entries(company_id=1)] == [1, 1, 1]
    # Messages are still shared.
    assert len(await event_db.fetch_all("SELECT id FROM rmm_event_log_messages")) == 1


def test_least_severe_levels_are_read_from_names_or_numbers(monkeypatch):
    assert [event_logs.parse_max_level(value) for value in ("3", " Warning ", "info", "verbose", "", None, "7", "loud")] == [
        3, 3, 4, 5, None, None, None, None]
    settings = event_logs.get_settings()
    assert event_logs.default_max_level() == 5  # everything unless EVENT_LOG_MIN_LEVEL says
    monkeypatch.setattr(settings, "event_log_min_level", "error")
    assert event_logs.default_max_level() == 2
    monkeypatch.setattr(settings, "event_log_min_level", "nonsense")
    assert event_logs.default_max_level() == 5
    assert event_logs.keeps_level(0, 1) and event_logs.keeps_level(1, 1) and not event_logs.keeps_level(2, 1)
    assert event_logs.keeps_level(200, 5) and not event_logs.keeps_level(5, 4)


@pytest.mark.anyio
async def test_companies_and_devices_choose_the_least_severe_level(sqlite_db, event_db, monkeypatch):
    monkeypatch.setattr(event_logs.get_settings(), "event_log_min_level", "information")
    _result, agent = await _enrol(sqlite_db)
    assert (await event_logs.agent_config(agent))["max_level"] == 4

    await event_logs.save_max_level("3", company_id=1, user_id=9)
    assert (await event_logs.agent_config(agent))["max_level"] == 3
    await event_logs.save_max_level("Error", asset_id=10, user_id=9)
    assert (await event_logs.agent_config(agent))["max_level"] == 2
    with pytest.raises(event_logs.EventLogSettingsError):
        await event_logs.save_max_level("loud", asset_id=10)

    # Older agents still send everything: the server drops what is less severe.
    batch = [_entry(1, level=4), _entry(2, level=3, event_id=1), _entry(3, level=2, event_id=2),
             _entry(4, log="System", level=0, event_id=3), _entry(5, level=5, event_id=4)]
    result = await event_logs.ingest(agent, batch)
    assert (result["stored"], result["below_level"]) == (2, 3)
    assert sorted(e["record_id"] for e in await event_log_repo.list_entries(company_id=1)) == [3, 4]

    # Extra logs and the level share a row; clearing both removes it.
    await event_logs.save_extra_logs("Security", asset_id=10)
    assert await event_log_repo.get_settings(asset_id=10) == {"extra_logs": ["Security"], "max_level": 2}
    await event_logs.save_extra_logs("", asset_id=10)
    assert (await event_log_repo.get_settings(asset_id=10))["max_level"] == 2
    await event_logs.save_max_level("", asset_id=10)
    assert await sqlite_db.fetch_one("SELECT scope_key FROM rmm_event_log_settings WHERE scope_key = 'asset:10'") is None
    assert (await event_logs.agent_config(agent))["max_level"] == 3


@pytest.mark.anyio
async def test_older_databases_are_upgraded_and_their_messages_moved(sqlite_db, tmp_path, monkeypatch):
    import aiosqlite

    path = str(tmp_path / "events.db")
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "CREATE TABLE rmm_event_log_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NULL, "
            "asset_id INTEGER NULL, agent_id INTEGER NOT NULL, log_name TEXT NOT NULL, record_id INTEGER NOT NULL, "
            "event_id INTEGER NOT NULL, level INTEGER NOT NULL DEFAULT 4, provider TEXT NULL, computer TEXT NULL, "
            "message TEXT NULL, time_created TEXT NOT NULL, received_at TEXT NOT NULL, UNIQUE (agent_id, log_name, record_id))"
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for record_id, message, days in ((1, "Old text", 1), (2, "Old text", 2), (3, None, 1), (4, "Ancient", 90)):
            await conn.execute(
                "INSERT INTO rmm_event_log_entries (company_id, asset_id, agent_id, log_name, record_id, event_id, "
                "message, time_created, received_at) VALUES (1, 10, 1, 'System', ?, 1, ?, ?, ?)",
                (record_id, message, str(now - timedelta(days=days)), str(now)),
            )
        await conn.commit()
    database = EventLogDatabase(sqlite_path=path)
    monkeypatch.setattr(event_log_repo, "event_log_db", database)
    monkeypatch.setattr(event_logs, "event_log_db", database)
    try:
        assert await database.connect()
        columns = {row["name"] for row in await database.fetch_all("PRAGMA table_info(rmm_event_log_entries)")}
        assert {"message_id", "repeat_count", "last_time_created", "last_record_id"} <= columns
        assert [e["message"] for e in await event_log_repo.list_entries(company_id=1, search="old")] == ["Old text", "Old text"]

        # The daily prune moves the old text into shared messages and drops what is too old.
        assert await event_logs.prune() == 1
        rows = await database.fetch_all("SELECT record_id, message, message_id, repeat_count FROM rmm_event_log_entries ORDER BY record_id")
        assert [(row["record_id"], row["message"], row["repeat_count"]) for row in rows] == [
            (1, None, 1), (2, None, 1), (3, None, 1)]
        assert rows[0]["message_id"] == rows[1]["message_id"] and rows[2]["message_id"] is None
        assert [e["message"] for e in await event_log_repo.list_entries(company_id=1, search="old")] == ["Old text", "Old text"]
        # The next prune only looks at entries added since.
        assert await event_log_repo.compact_legacy_messages() == 0
    finally:
        await database.disconnect()


@pytest.mark.anyio
async def test_messages_no_entry_uses_are_pruned(sqlite_db, event_db):
    _result, agent = await _enrol(sqlite_db)
    await event_logs.ingest(agent, [_entry(1, message="Old", minutes_ago=60 * 24 * 40), _entry(2, message="New", event_id=1)])
    long_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=40)
    await event_db.execute_rowcount("UPDATE rmm_event_log_messages SET last_seen_at = %s WHERE message = 'Old'", (long_ago,))
    assert await event_logs.prune() == 1
    assert [row["message"] for row in await event_db.fetch_all("SELECT message FROM rmm_event_log_messages")] == ["New"]


def test_event_logs_page_shows_levels_and_repeats():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    html = env.get_template("rmm/event_logs.html").render(
        company={"name": "Contoso"},
        database={"configured": True, "connected": True, "error": None},
        default_logs=["Application", "System"], company_logs=[],
        devices=[{"asset_id": 10, "asset_name": "PC-01", "hostname": "pc", "os": "windows", "extra_logs": [], "max_level": 2}],
        log_choices=["System"], level_filters=[],
        entries=[{"asset_id": 10, "log_name": "System", "level_label": "Information", "provider": "SCM", "event_id": 7036,
                  "message": "Service started", "time_created": "2026-10-11T10:00:00", "repeat_count": 12,
                  "last_time_created": "2026-10-11T10:55:00", "computer": "PC-01"}],
        filters={"asset_id": None, "log": "", "level": "", "event_id": "", "q": "", "limit": 200},
        can_edit=True, retention_days=30, csrf_token="tok",
        max_level_choices=event_logs.MAX_LEVEL_CHOICES, level_labels=dict(event_logs.MAX_LEVEL_CHOICES),
        default_max_level=5, company_max_level=3, inherited_max_level=3,
    )
    assert "12 times" in html and 'data-utc="2026-10-11T10:55:00"' in html
    assert '<option value="3" selected>Warning or worse</option>' in html
    assert '<option value="2" selected>Error or worse</option>' in html
    assert "Company: Warning or worse" in html and "Default: Everything (verbose)" in html
