"""Tests for service contracts and agreements."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import ImmutableMultiDict

from app.core.database import db
from app.features.agreements import PACK
from app.repositories import service_agreements as repo
from app.services import service_agreements as agreements

MIGRATION = Path("migrations/581_service_agreements.sql")
TODAY = date(2026, 11, 26)


def _agreement(**overrides):
    base = {"id": 1, "company_id": 5, "name": "Support", "agreement_type": "hours", "hours_allowance": 10.0,
            "period": "monthly", "start_date": date(2026, 1, 1), "end_date": None, "fee_amount": 1000.0,
            "alert_percent": 80, "is_cancelled": False, "labour_type_ids": [], "company_name": "Acme"}
    base.update(overrides)
    return base


def _entry(entry_id, minutes, when, *, company_id=5, labour_type_id=1, rate=100):
    return {"id": entry_id, "ticket_id": 9, "minutes_spent": minutes, "labour_type_id": labour_type_id,
            "created_at": when, "company_id": company_id, "labour_rate": rate}


def test_pack_registers_routes_and_job():
    paths = {route.path for router in PACK.routers for route in router.routes}
    assert {"/admin/agreements", "/admin/agreements/{agreement_id:int}", "/admin/agreements/{agreement_id:int}/delete",
            "/api/agreements", "/api/agreements/{agreement_id}"} <= paths
    assert PACK.background_jobs == (agreements.alert_job,)


def test_migration_runs_on_sqlite():
    adapted = db._adapt_sql_for_sqlite(MIGRATION.read_text(encoding="utf-8"))
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE companies (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE ticket_labour_types (id INTEGER PRIMARY KEY)")
    for statement in db._split_sql_statements(adapted):
        connection.execute(statement)
    connection.execute("INSERT INTO service_agreements (company_id, name, start_date) VALUES (1, 'x', '2026-01-01')")
    assert connection.execute("SELECT agreement_type, period, alert_percent FROM service_agreements").fetchone() == (
        "hours", "monthly", 80)
    connection.execute("INSERT INTO service_agreement_alerts (agreement_id, kind, period_start) VALUES (1, 'x', '2026-01-01')")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO service_agreement_alerts (agreement_id, kind, period_start) VALUES (1, 'x', '2026-01-01')")


@pytest.mark.parametrize(
    ("overrides", "on", "expected"),
    [
        ({}, date(2026, 11, 26), (date(2026, 11, 1), date(2026, 12, 1))),
        ({"start_date": date(2026, 11, 15)}, date(2026, 11, 26), (date(2026, 11, 15), date(2026, 12, 1))),
        ({"end_date": date(2026, 11, 20)}, date(2026, 11, 10), (date(2026, 11, 1), date(2026, 11, 21))),
        ({"period": "quarterly"}, date(2026, 11, 26), (date(2026, 10, 1), date(2027, 1, 1))),
        ({"period": "annual", "start_date": date(2025, 3, 15)}, date(2026, 11, 26), (date(2026, 3, 15), date(2027, 3, 15))),
        ({"period": "annual", "start_date": date(2025, 3, 15)}, date(2026, 3, 14), (date(2025, 3, 15), date(2026, 3, 15))),
        ({"agreement_type": "block", "start_date": date(2026, 6, 1), "end_date": date(2027, 5, 31)}, date(2026, 11, 26),
         (date(2026, 6, 1), date(2027, 6, 1))),
    ],
)
def test_period_for(overrides, on, expected):
    assert agreements.period_for(_agreement(**overrides), on) == expected


def test_recent_periods_newest_first_and_stops_at_start():
    periods = agreements.recent_periods(_agreement(start_date=date(2026, 9, 10)), today=TODAY)
    assert periods == [(date(2026, 11, 1), date(2026, 12, 1)), (date(2026, 10, 1), date(2026, 11, 1)),
                       (date(2026, 9, 10), date(2026, 10, 1))]
    assert agreements.recent_periods(_agreement(start_date=date(2027, 1, 1)), today=TODAY) == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [({}, "active"), ({"is_cancelled": True}, "cancelled"), ({"start_date": date(2027, 1, 1)}, "upcoming"),
     ({"end_date": date(2026, 11, 1)}, "ended"), ({"end_date": date(2026, 12, 20)}, "expiring")],
)
def test_status(overrides, expected):
    assert agreements.status(_agreement(**overrides), today=TODAY) == expected


def test_hours_agreement_draws_down_then_counts_overage():
    usage = agreements.assign_entries([_agreement()], [
        _entry(1, 300, datetime(2026, 11, 2, 9)),
        _entry(2, 240, datetime(2026, 11, 3, 9)),
        _entry(3, 120, datetime(2026, 11, 4, 9)),
        _entry(4, 60, datetime(2026, 10, 30, 9)),  # last month: its own period
        _entry(5, 60, datetime(2026, 11, 5, 9), company_id=6),  # another company
    ])
    november = usage[(1, date(2026, 11, 1))]
    assert november.covered_minutes == 600 and november.overage_minutes == 60
    assert november.percent == 110 and november.remaining_minutes == 0
    assert november.value == pytest.approx(1000.0)
    assert usage[(1, date(2026, 10, 1))].covered_minutes == 60


def test_unlimited_covers_first_and_blocks_last():
    managed = _agreement(id=1, agreement_type="unlimited", hours_allowance=None, labour_type_ids=[1])
    block = _agreement(id=2, agreement_type="block", hours_allowance=5, start_date=date(2026, 1, 1))
    usage = agreements.assign_entries([block, managed], [
        _entry(1, 120, datetime(2026, 11, 2), labour_type_id=1),   # remote: managed covers it
        _entry(2, 90, datetime(2026, 11, 3), labour_type_id=2),    # onsite: only the block covers it
    ])
    assert usage[(1, date(2026, 11, 1))].covered_minutes == 120
    assert usage[(2, date(2026, 1, 1))].covered_minutes == 90
    assert usage[(2, date(2026, 1, 1))].remaining_minutes == 210


def test_labour_type_limits_and_inactive_agreements_cover_nothing():
    limited = _agreement(labour_type_ids=[3])
    cancelled = _agreement(id=2, is_cancelled=True)
    upcoming = _agreement(id=3, start_date=date(2027, 1, 1))
    usage = agreements.assign_entries([limited, cancelled, upcoming], [
        _entry(1, 60, datetime(2026, 11, 2), labour_type_id=1),
        _entry(2, 60, datetime(2026, 11, 2), labour_type_id=None),
    ])
    assert usage == {}


def test_full_hours_agreement_spills_into_the_next_one_before_overage():
    first = _agreement(id=1, hours_allowance=1)
    second = _agreement(id=2, hours_allowance=1, start_date=date(2026, 2, 1))
    usage = agreements.assign_entries([first, second], [
        _entry(1, 60, datetime(2026, 11, 2)), _entry(2, 60, datetime(2026, 11, 3)), _entry(3, 30, datetime(2026, 11, 4)),
    ])
    assert usage[(1, date(2026, 11, 1))].covered_minutes == 60
    assert usage[(2, date(2026, 11, 1))].covered_minutes == 60
    assert usage[(2, date(2026, 11, 1))].overage_minutes == 30


def test_summary_flags_and_effective_rate():
    usage = agreements.assign_entries([_agreement()], [_entry(1, 510, datetime(2026, 11, 2))])
    item = agreements.summary(_agreement(), usage[(1, date(2026, 11, 1))], today=TODAY)
    assert item["flags"] == ["near_limit"] and item["percent"] == 85
    assert item["used_text"] == "8.5 h" and item["remaining_text"] == "1.5 h"
    assert item["effective_rate"] == pytest.approx(117.65)
    empty = agreements.summary(_agreement(agreement_type="unlimited", hours_allowance=None), None, today=TODAY)
    assert empty["allowance_text"] == "Unlimited" and empty["flags"] == [] and empty["effective_rate"] is None


def _form(**values):
    items = []
    for key, value in values.items():
        for item in value if isinstance(value, list) else [value]:
            items.append((key, str(item)))
    return ImmutableMultiDict(items)


def test_parse_form():
    values = agreements.parse_form(_form(name="  Remote  support ", agreement_type="hours", hours_allowance="7.5",
                                         period="quarterly", start_date="2026-12-01", end_date="2027-11-30",
                                         fee_amount="1,250.00", alert_percent="90", labour_type_ids=["1", "2"]),
                                   valid_labour_type_ids=[1, 2, 3])
    assert values["name"] == "Remote support" and float(values["hours_allowance"]) == 7.5
    assert values["period"] == "quarterly" and values["labour_type_ids"] == [1, 2]
    assert float(values["fee_amount"]) == 1250 and values["end_date"] == date(2027, 11, 30)
    unlimited = agreements.parse_form(_form(name="x", agreement_type="unlimited", start_date="2026-01-01"),
                                      valid_labour_type_ids=[])
    assert unlimited["hours_allowance"] is None


def test_parse_form_reports_every_problem():
    with pytest.raises(agreements.AgreementError) as excinfo:
        agreements.parse_form(_form(name="", agreement_type="hours", hours_allowance="-1", period="weekly",
                                    start_date="2026-12-01", end_date="2026-11-01", fee_amount="lots",
                                    alert_percent="0", labour_type_ids=["9"]), valid_labour_type_ids=[1])
    assert set(excinfo.value.errors) == {"name", "hours_allowance", "period", "end_date", "fee_amount",
                                         "alert_percent", "labour_type_ids"}


def test_describe():
    assert agreements.describe({"agreement_type": "unlimited", "labour_type_ids": [1]}, {1: "Remote"}) == (
        "All support time for Remote work is covered.")
    assert agreements.describe({"agreement_type": "hours", "hours_allowance": 10, "period": "quarterly"}) == (
        "10 hours are included each quarter.")


@pytest.mark.asyncio
async def test_alerts_fire_once_per_period(monkeypatch):
    from app.services import automations

    item = agreements.summary(_agreement(), agreements.assign_entries(
        [_agreement()], [_entry(1, 600, datetime(2026, 11, 2))])[(1, date(2026, 11, 1))], today=TODAY)
    expiring = agreements.summary(_agreement(id=2, agreement_type="unlimited", hours_allowance=None,
                                             end_date=date(2026, 12, 10)), None, today=TODAY)
    monkeypatch.setattr(agreements, "list_summaries", AsyncMock(return_value=[item, expiring]))
    recorded = AsyncMock(side_effect=[True, True, False, False])
    monkeypatch.setattr(repo, "record_alert", recorded)
    handle = AsyncMock()
    monkeypatch.setattr(automations, "handle_event", handle)
    assert await agreements.check_alerts(today=TODAY) == 2
    events = [call.args[0] for call in handle.await_args_list]
    assert events == ["agreements.exhausted", "agreements.expiring"]
    assert handle.await_args_list[0].args[1]["agreement"]["url"] == "/admin/agreements/1"
    assert await agreements.check_alerts(today=TODAY) == 0


def test_agreement_events_are_automation_triggers():
    from app.services import automations

    values = {event["value"] for event in automations.list_trigger_events()}
    assert set(agreements.ALERT_EVENTS.values()) <= values


@pytest.mark.asyncio
async def test_edit_routes_require_super_admin(monkeypatch):
    from fastapi.responses import RedirectResponse

    from app.features.agreements import routes

    redirect = RedirectResponse("/login")
    main = SimpleNamespace(_require_super_admin_page=AsyncMock(return_value=(None, redirect)))
    monkeypatch.setattr(routes, "_main", lambda: main)
    create = AsyncMock()
    monkeypatch.setattr(repo, "create_agreement", create)
    assert await routes.create_agreement(SimpleNamespace()) is redirect
    assert await routes.delete_agreement(1, SimpleNamespace()) is redirect
    create.assert_not_awaited()


def test_templates_compile_and_post_with_csrf():
    import app.main as main_module

    for name in ("admin/agreements.html", "admin/_agreement_editor.html", "admin/agreement_detail.html"):
        main_module.templates.env.get_template(name)
    editor = Path("app/templates/admin/_agreement_editor.html").read_text(encoding="utf-8")
    assert editor.count('method="post"') == editor.count('{% include "partials/csrf.html" %}')
    assert "<dialog" not in editor and "onclick=" not in editor


@pytest.mark.asyncio
async def test_repository_queries_bind_every_value(monkeypatch):
    calls = []

    def record(result):
        async def capture(sql, params=None):
            calls.append((sql, tuple(params or ())))
            return result
        return capture

    async def capture_many(sql, rows):
        for row in rows:
            calls.append((sql, tuple(row)))

    monkeypatch.setattr(db, "fetch_all", record([]))
    monkeypatch.setattr(db, "fetch_one", record(None))
    monkeypatch.setattr(db, "execute", record(None))
    monkeypatch.setattr(db, "execute_returning_lastrowid", record(1))
    monkeypatch.setattr(db, "execute_many", capture_many)
    values = {"company_id": 5, "name": "x", "agreement_type": "hours", "hours_allowance": 1, "period": "monthly",
              "start_date": TODAY, "end_date": None, "fee_amount": None, "alert_percent": 80, "notes": None,
              "is_cancelled": False, "labour_type_ids": [1, 2]}
    await repo.list_agreements(company_id=5, include_cancelled=False)
    await repo.create_agreement(values, user_id=1)
    await repo.update_agreement(1, values)
    await repo.list_time_entries([5, 6], datetime(2026, 1, 1), datetime(2026, 2, 1))
    await repo.record_alert(1, "usage_threshold", TODAY)
    await repo._attach_labour_types([{"id": 1}, {"id": 2}])
    for sql, params in calls:
        assert sql.count("%s") == len(params), sql
