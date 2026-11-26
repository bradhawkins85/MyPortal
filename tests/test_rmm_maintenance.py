"""Tests for RMM maintenance windows and alert snoozes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import ImmutableMultiDict

from app.core.database import db
from app.features.rmm import PACK
from app.repositories import rmm_maintenance as repo
from app.services import rmm_checks
from app.services import rmm_maintenance as maintenance

MIGRATION = Path("migrations/579_rmm_maintenance_windows.sql")
NOW = datetime(2026, 11, 25, 23, 30)  # a Wednesday, UTC


@pytest.fixture(autouse=True)
def _clear_cache():
    maintenance.clear_cache()
    yield
    maintenance.clear_cache()


def _weekly(**overrides):
    window = {"id": 1, "name": "Patch night", "schedule_type": "weekly", "weekdays": "wed", "start_time": "22:00",
              "duration_minutes": 240, "timezone": "UTC", "is_enabled": True, "company_id": 5, "asset_id": None,
              "check_id": None}
    window.update(overrides)
    return window


def _once(**overrides):
    window = {"id": 2, "name": "Server move", "schedule_type": "once", "starts_at": NOW - timedelta(hours=1),
              "ends_at": NOW + timedelta(hours=1), "is_enabled": True, "company_id": 5, "asset_id": None,
              "check_id": None}
    window.update(overrides)
    return window


def test_pack_registers_routes_and_tick():
    paths = {route.path for router in PACK.routers for route in router.routes}
    assert {"/rmm/maintenance", "/rmm/maintenance/snooze", "/rmm/maintenance/{window_id:int}",
            "/rmm/maintenance/{window_id:int}/end", "/rmm/maintenance/{window_id:int}/delete",
            "/api/rmm/maintenance-windows", "/api/rmm/maintenance-windows/{window_id}",
            "/api/rmm/assets/{asset_id}/maintenance"} <= paths


def test_migration_runs_on_sqlite():
    adapted = db._adapt_sql_for_sqlite(MIGRATION.read_text(encoding="utf-8"))
    connection = sqlite3.connect(":memory:")
    for table in ("companies", "assets", "rmm_checks"):
        connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    for statement in db._split_sql_statements(adapted):
        connection.execute(statement)
    connection.execute("INSERT INTO rmm_maintenance_windows (name) VALUES ('x')")
    row = connection.execute("SELECT schedule_type, is_enabled, is_snooze FROM rmm_maintenance_windows").fetchone()
    assert row == ("once", 1, 0)


def test_one_off_window_is_active_between_start_and_end():
    window = _once()
    assert maintenance.active_until(window, NOW) == window["ends_at"]
    assert maintenance.active_until(window, window["ends_at"]) is None
    assert maintenance.state(window, NOW) == "active"
    assert maintenance.state(_once(starts_at=NOW + timedelta(hours=1), ends_at=NOW + timedelta(hours=2)), NOW) == "upcoming"
    assert maintenance.state(_once(ends_at=NOW - timedelta(minutes=1)), NOW) == "ended"
    assert maintenance.state(_once(is_enabled=False), NOW) == "off"


def test_weekly_window_runs_past_midnight():
    window = _weekly()
    assert maintenance.active_until(window, NOW) == datetime(2026, 11, 26, 2, 0)
    # Thursday 01:59 UTC is still inside Wednesday's 22:00 + 4h occurrence.
    assert maintenance.active_until(window, datetime(2026, 11, 26, 1, 59)) is not None
    assert maintenance.active_until(window, datetime(2026, 11, 26, 2, 0)) is None
    assert maintenance.active_until(window, datetime(2026, 11, 25, 21, 59)) is None
    assert maintenance.next_start(window, datetime(2026, 11, 26, 3, 0)) == datetime(2026, 12, 2, 22, 0)
    assert maintenance.state(window, datetime(2026, 11, 26, 3, 0)) == "recurring"


def test_weekly_window_uses_its_time_zone():
    # 22:00 in Brisbane (UTC+10, no daylight saving) is 12:00 UTC.
    window = _weekly(timezone="Australia/Brisbane", duration_minutes=60)
    assert maintenance.active_until(window, datetime(2026, 11, 25, 12, 30)) == datetime(2026, 11, 25, 13, 0)
    assert maintenance.active_until(window, NOW) is None


@pytest.mark.parametrize(
    ("window", "company_id", "asset_id", "check_id", "expected"),
    [
        ({"company_id": None, "asset_id": None, "check_id": None}, 9, 1, 3, True),
        ({"company_id": 5, "asset_id": None, "check_id": None}, 9, 1, 3, False),
        ({"company_id": 5, "asset_id": 1, "check_id": None}, 5, 1, 3, True),
        ({"company_id": 5, "asset_id": 1, "check_id": None}, 5, 2, 3, False),
        ({"company_id": 5, "asset_id": 1, "check_id": 3}, 5, 1, 3, True),
        ({"company_id": 5, "asset_id": 1, "check_id": 3}, 5, 1, 4, False),
        ({"company_id": 5, "asset_id": 1, "check_id": 3}, 5, 1, None, False),
    ],
)
def test_covers_scope(window, company_id, asset_id, check_id, expected):
    assert maintenance.covers(window, company_id=company_id, asset_id=asset_id, check_id=check_id) is expected


@pytest.mark.asyncio
async def test_coverage_is_cached_and_reports_latest_end(monkeypatch):
    lister = AsyncMock(return_value=[_once(), _once(id=3, ends_at=NOW + timedelta(hours=3)), _weekly(company_id=6)])
    monkeypatch.setattr(repo, "list_enabled", lister)
    found = await maintenance.coverage(company_id=5, asset_id=1, now=NOW)
    assert [window["id"] for window in found.windows] == [2, 3]
    assert found.until == NOW + timedelta(hours=3)
    await maintenance.coverage(company_id=5, asset_id=1, now=NOW)
    assert lister.await_count == 1
    maintenance.clear_cache()
    await maintenance.coverage(company_id=5, asset_id=1, now=NOW)
    assert lister.await_count == 2


def _form(**values):
    items = []
    for key, value in values.items():
        for item in value if isinstance(value, list) else [value]:
            items.append((key, item))
    return ImmutableMultiDict(items)


def test_parse_form_one_off_converts_local_time_to_utc():
    values = maintenance.parse_form(_form(
        name=" Server   move ", schedule_type="once", timezone="Australia/Brisbane",
        starts_at="2026-12-01T20:00", ends_at="2026-12-01T23:00", is_enabled=["0", "1"],
    ), now=NOW)
    assert values["name"] == "Server move"
    assert values["starts_at"] == datetime(2026, 12, 1, 10, 0)
    assert values["ends_at"] == datetime(2026, 12, 1, 13, 0)
    assert values["is_enabled"] is True


def test_parse_form_reports_every_problem():
    with pytest.raises(maintenance.WindowError) as excinfo:
        maintenance.parse_form(_form(name="", schedule_type="weekly", timezone="Mars/Base", duration_minutes="1"),
                               now=NOW)
    assert set(excinfo.value.errors) == {"name", "timezone", "weekdays", "start_time", "duration_minutes"}


def test_parse_form_rejects_backwards_and_past_one_off_windows():
    with pytest.raises(maintenance.WindowError) as excinfo:
        maintenance.parse_form(_form(name="x", starts_at="2026-12-02T10:00", ends_at="2026-12-02T09:00"), now=NOW)
    assert "ends_at" in excinfo.value.errors
    with pytest.raises(maintenance.WindowError):
        maintenance.parse_form(_form(name="x", starts_at="2026-01-01T10:00", ends_at="2026-01-01T11:00"), now=NOW)
    # Editing an existing window that has already ended is allowed.
    maintenance.parse_form(_form(name="x", id="4", starts_at="2026-01-01T10:00", ends_at="2026-01-01T11:00"), now=NOW)


def test_parse_form_switched_off_window():
    values = maintenance.parse_form(_form(name="x", schedule_type="weekly", weekdays=["sat", "sun"],
                                          start_time="01:00", duration_minutes="120", is_enabled="0"), now=NOW)
    assert values["is_enabled"] is False
    assert values["weekdays"] == "sat,sun"
    assert maintenance.describe_schedule(values) == "Weekends from 01:00 for 2 hours (UTC)"


def test_snooze_only_accepts_offered_lengths():
    values = maintenance.snooze_values(name="Snoozed", company_id=5, asset_id=1, check_id=None, minutes=60,
                                       reason=None, now=NOW)
    assert values["is_snooze"] and values["ends_at"] == NOW + timedelta(hours=1)
    with pytest.raises(maintenance.WindowError):
        maintenance.snooze_values(name="x", company_id=5, asset_id=1, check_id=None, minutes=99999, reason=None)


def test_only_super_admins_edit_every_company_windows():
    assert maintenance.can_edit({"company_id": None}, company_id=5, is_super_admin=True)
    assert not maintenance.can_edit({"company_id": None}, company_id=5, is_super_admin=False)
    assert maintenance.can_edit({"company_id": 5}, company_id=5, is_super_admin=False)
    assert not maintenance.can_edit({"company_id": 6}, company_id=5, is_super_admin=True)


# --------------------------------------------------------------------------- #
# Checks hold their actions during maintenance
# --------------------------------------------------------------------------- #

CHECK = {"id": 7, "name": "Disk C:", "check_type": "disk", "duration_minutes": 0, "cooldown_minutes": 0,
         "actions": [{"key": "a1", "type": "create_ticket"}], "action": "create_ticket"}
AGENT = {"id": 3, "company_id": 5, "asset_id": 1, "hostname": "srv"}


@pytest.fixture
def check_mocks(monkeypatch):
    from app.repositories import rmm_checks as checks_repo
    from app.services import rmm_action_steps

    monkeypatch.setattr(rmm_action_steps, "steps", lambda check: list(check.get("actions") or []))
    act = AsyncMock(return_value=[])
    monkeypatch.setattr(rmm_checks, "_act_on_failure", act)
    record_failure = AsyncMock()
    monkeypatch.setattr(checks_repo, "record_failure", record_failure)
    monkeypatch.setattr(checks_repo, "save_state", AsyncMock())
    monkeypatch.setattr(checks_repo, "get_state", AsyncMock(return_value=None))
    return SimpleNamespace(act=act, record_failure=record_failure)


@pytest.mark.asyncio
async def test_failing_check_holds_actions_during_maintenance(monkeypatch, check_mocks):
    monkeypatch.setattr(repo, "list_enabled", AsyncMock(return_value=[_once()]))
    state = await rmm_checks.record_result(CHECK, AGENT, True, "2% free", now=NOW)
    assert state["status"] == "failing"
    check_mocks.act.assert_not_awaited()
    assert "Actions held" in check_mocks.record_failure.await_args.args[1]


@pytest.mark.asyncio
async def test_held_actions_run_once_the_window_ends(monkeypatch, check_mocks):
    monkeypatch.setattr(repo, "list_enabled", AsyncMock(return_value=[_once()]))
    state = await rmm_checks.record_result(CHECK, AGENT, True, "2% free", now=NOW)
    maintenance.clear_cache()
    later = NOW + timedelta(hours=2)
    await rmm_checks.record_result(CHECK, AGENT, True, "2% free", now=later, state=dict(state))
    check_mocks.act.assert_awaited_once()


@pytest.mark.asyncio
async def test_window_for_another_check_does_not_hold(monkeypatch, check_mocks):
    monkeypatch.setattr(repo, "list_enabled", AsyncMock(return_value=[_once(check_id=99)]))
    await rmm_checks.record_result(CHECK, AGENT, True, "2% free", now=NOW)
    check_mocks.act.assert_awaited_once()


@pytest.mark.asyncio
async def test_lookup_failure_never_loses_an_alert(monkeypatch, check_mocks):
    monkeypatch.setattr(repo, "list_enabled", AsyncMock(side_effect=RuntimeError("db down")))
    await rmm_checks.record_result(CHECK, AGENT, True, "2% free", now=NOW)
    check_mocks.act.assert_awaited_once()


@pytest.mark.asyncio
async def test_event_log_rule_raises_no_ticket_during_maintenance(monkeypatch):
    from app.services import rmm_actions, rmm_event_log_rules

    monkeypatch.setattr(repo, "list_enabled", AsyncMock(return_value=[_once()]))
    monkeypatch.setattr(maintenance, "_utcnow", lambda: NOW)
    raise_ticket = AsyncMock()
    monkeypatch.setattr(rmm_actions, "raise_ticket", raise_ticket)
    text, is_new = await rmm_event_log_rules._ticket_step({"id": 4, "name": "Lockouts"}, AGENT, [{"message": "x"}])
    raise_ticket.assert_not_awaited()
    assert is_new is False and "maintenance" in text


# --------------------------------------------------------------------------- #
# Routes and templates
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_non_super_admin_cannot_target_every_company(monkeypatch):
    from app.features.rmm import maintenance_routes

    with pytest.raises(maintenance.WindowError) as excinfo:
        await maintenance_routes._resolve_scope(_form(scope="all"), company_id=5, is_super_admin=False)
    assert "scope" in excinfo.value.errors


@pytest.mark.asyncio
async def test_device_scope_must_belong_to_the_company(monkeypatch):
    from app.features.rmm import maintenance_routes
    from app.repositories import assets as assets_repo

    monkeypatch.setattr(assets_repo, "get_asset_by_id", AsyncMock(return_value={"id": 1, "company_id": 6}))
    with pytest.raises(maintenance.WindowError) as excinfo:
        await maintenance_routes._resolve_scope(_form(scope="device", asset_id="1"), company_id=5,
                                                is_super_admin=True)
    assert "asset_id" in excinfo.value.errors


@pytest.mark.asyncio
async def test_window_from_another_company_is_not_found(monkeypatch):
    from fastapi import HTTPException

    from app.features.rmm import maintenance_routes

    monkeypatch.setattr(repo, "get", AsyncMock(return_value={"id": 1, "company_id": 6}))
    with pytest.raises(HTTPException) as excinfo:
        await maintenance_routes._window_for(1, {"is_super_admin": True}, 5, edit=False)
    assert excinfo.value.status_code == 404


def test_maintenance_template_compiles_and_has_csrf():
    import app.main as main_module

    main_module.templates.env.get_template("rmm/maintenance.html")
    source = Path("app/templates/rmm/maintenance.html").read_text(encoding="utf-8")
    assert source.count('{% include "partials/csrf.html" %}') == 3
    assert "<dialog" not in source and "onclick=" not in source


def test_asset_page_offers_snooze_with_csrf():
    source = Path("app/templates/assets/detail.html").read_text(encoding="utf-8")
    snooze = source[source.index('action="/rmm/maintenance/snooze"'):]
    assert '{% include "partials/csrf.html" %}' in snooze[:400]


@pytest.mark.asyncio
async def test_repository_queries_bind_every_value(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "fetch_all", AsyncMock(side_effect=lambda sql, params=None: calls.append((sql, params or ())) or []))
    monkeypatch.setattr(db, "fetch_one", AsyncMock(side_effect=lambda sql, params=None: calls.append((sql, params or ()))))
    monkeypatch.setattr(db, "execute", AsyncMock(side_effect=lambda sql, params=None: calls.append((sql, params or ()))))
    monkeypatch.setattr(db, "execute_rowcount", AsyncMock(side_effect=lambda sql, params=None: calls.append((sql, params or ())) or 0))
    monkeypatch.setattr(db, "execute_returning_lastrowid", AsyncMock(side_effect=lambda sql, params=None: calls.append((sql, params or ())) or 1))
    values = maintenance.snooze_values(name="x", company_id=5, asset_id=1, check_id=None, minutes=60, reason=None)
    await repo.list_for_company(5)
    await repo.get(1)
    await repo.create(values, created_by_user_id=2)
    await repo.update(1, values)
    await repo.end_now(1, NOW)
    await repo.delete(1)
    await repo.delete_ended_snoozes(NOW)
    for sql, params in calls:
        assert sql.count("%s") == len(params), sql
