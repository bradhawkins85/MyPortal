"""Tests for technician scheduling and dispatch."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import ImmutableMultiDict

from app.core.database import db
from app.features.scheduling import PACK
from app.repositories import appointments as repo
from app.services import scheduling

MIGRATION = Path("migrations/582_technician_scheduling.sql")
BRISBANE = scheduling.Settings(timezone="Australia/Brisbane", day_start_hour=8, day_end_hour=18)


def _form(**values):
    return ImmutableMultiDict([(key, str(value)) for key, value in values.items()])


def _appointment(**overrides):
    base = {"id": 7, "ticket_id": 42, "ticket_number": "T42", "ticket_subject": "Printer jam", "company_id": 5,
            "company_name": "Acme", "technician_user_id": 3, "technician_first_name": "Sam", "technician_last_name": "Lee",
            "technician_email": "sam@msp.example", "kind": "onsite", "starts_at": datetime(2026, 12, 1, 0, 0),
            "ends_at": datetime(2026, 12, 1, 1, 30), "location": "12 High St", "notes": "Bring toner", "status": "scheduled",
            "m365_event_id": None, "m365_mailbox": None, "sync_status": "pending", "sync_error": None}
    base.update(overrides)
    return base


def test_pack_registers_routes_and_job():
    paths = {route.path for router in PACK.routers for route in router.routes}
    assert {"/admin/schedule", "/admin/schedule/me", "/admin/schedule/appointments",
            "/admin/schedule/appointments/{appointment_id:int}", "/admin/schedule/appointments/{appointment_id:int}/status",
            "/admin/schedule/appointments/{appointment_id:int}/sync", "/admin/schedule/settings", "/api/appointments",
            "/api/appointments/{appointment_id}", "/api/tickets/{ticket_id}/appointments",
            "/api/appointments/{appointment_id}/cancel", "/api/appointments/{appointment_id}/move"} <= paths
    assert PACK.background_jobs == (scheduling.sync_job,)


def test_migration_runs_on_sqlite():
    adapted = db._adapt_sql_for_sqlite(MIGRATION.read_text(encoding="utf-8"))
    connection = sqlite3.connect(":memory:")
    for table in ("tickets", "companies", "users"):
        connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    for statement in db._split_sql_statements(adapted):
        connection.execute(statement.replace("INSERT IGNORE INTO", "INSERT OR IGNORE INTO"))
    assert connection.execute("SELECT day_start_hour, day_end_hour, default_duration_minutes FROM scheduling_settings").fetchone() == (7, 19, 60)
    connection.execute("INSERT INTO appointments (ticket_id, technician_user_id, starts_at, ends_at) VALUES (1, 1, '2026-01-01', '2026-01-01')")
    assert connection.execute("SELECT kind, status, sync_status FROM appointments").fetchone() == ("onsite", "scheduled", "off")


def test_parse_form_converts_local_time_to_utc():
    values = scheduling.parse_form(_form(ticket_id="#42", technician_user_id="3", kind="remote", date="2026-12-01",
                                         start_time="10:00", duration_minutes="90", location="  Reception  ",
                                         notes=" Call first "), BRISBANE, technician_ids=[3, 4])
    assert values["starts_at"] == datetime(2026, 12, 1, 0, 0)  # 10:00 AEST is midnight UTC
    assert values["ends_at"] == datetime(2026, 12, 1, 1, 30)
    assert values["location"] == "Reception" and values["notes"] == "Call first" and values["kind"] == "remote"


def test_parse_form_reports_every_problem():
    with pytest.raises(scheduling.AppointmentError) as excinfo:
        scheduling.parse_form(_form(ticket_id="x", technician_user_id="9", kind="teleport", date="soon",
                                    start_time="25:99", duration_minutes="1"), BRISBANE, technician_ids=[3])
    assert set(excinfo.value.errors) == {"ticket_id", "technician_user_id", "kind", "date", "start_time",
                                         "duration_minutes"}


def test_parse_settings_form():
    values = scheduling.parse_settings_form(_form(timezone="Australia/Perth", day_start_hour="6", day_end_hour="20",
                                                  default_duration_minutes="30", m365_company_id="5"))
    assert values == {"m365_company_id": 5, "timezone": "Australia/Perth", "day_start_hour": 6, "day_end_hour": 20,
                      "default_duration_minutes": 30}
    with pytest.raises(scheduling.AppointmentError) as excinfo:
        scheduling.parse_settings_form(_form(timezone="Nowhere/City", day_start_hour="18", day_end_hour="8",
                                             default_duration_minutes="0"))
    assert set(excinfo.value.errors) == {"timezone", "day_end_hour", "default_duration_minutes"}


def test_describe_when_in_local_time():
    assert scheduling.describe_when(_appointment(), BRISBANE.zone) == "Tue 01 Dec 2026, 10:00–11:30"


def test_board_positions_blocks_and_stacks_overlaps():
    technicians = [{"id": 3, "first_name": "Sam", "last_name": "Lee"}, {"id": 4, "email": "kim@msp.example"}]
    appointments = [
        _appointment(),  # 10:00–11:30 local
        _appointment(id=8, starts_at=datetime(2026, 12, 1, 1, 0), ends_at=datetime(2026, 12, 1, 2, 0)),  # 11:00–12:00
        _appointment(id=9, starts_at=datetime(2026, 12, 1, 20, 0), ends_at=datetime(2026, 12, 1, 21, 0)),  # next day local
    ]
    board = scheduling.board(date(2026, 12, 1), technicians, appointments, BRISBANE)
    sam, kim = board["rows"]
    assert sam["name"] == "Sam Lee" and kim["name"] == "kim@msp.example"
    assert [item["id"] for item in sam["items"]] == [7, 8]
    first, second = sam["items"]
    assert first["left"] == 20.0 and first["width"] == 15.0  # 10:00 on an 08:00–18:00 board
    assert first["lane"] == 0 and second["lane"] == 1 and sam["lanes"] == 2
    assert kim["items"] == [] and board["hours"][0] == {"label": "08:00", "left": 0.0}


def test_event_payload_escapes_and_links_ticket():
    payload = scheduling.event_payload(_appointment(notes="<b>x</b>", company_name="A & B"),
                                       portal_url="https://portal.example/")
    assert payload["subject"] == "Onsite visit: Printer jam (#T42)"
    assert payload["start"] == {"dateTime": "2026-12-01T00:00:00", "timeZone": "UTC"}
    assert "&lt;b&gt;x&lt;/b&gt;" in payload["body"]["content"] and "A &amp; B" in payload["body"]["content"]
    assert "https://portal.example/admin/tickets/42" in payload["body"]["content"]
    assert payload["location"] == {"displayName": "12 High St"}


class _FakeM365:
    class M365Error(Exception):
        def __init__(self, message, http_status=None):
            super().__init__(message)
            self.http_status = http_status

    def __init__(self):
        self.calls = []
        self.post_result = {"id": "evt-1"}
        self.fail = None

    async def acquire_access_token(self, company_id, *, force_client_credentials=False):
        return "token"

    async def _graph_post(self, token, url, payload):
        self.calls.append(("post", url, payload))
        if self.fail:
            raise self.fail
        return self.post_result

    async def _graph_patch(self, token, url, payload):
        self.calls.append(("patch", url, payload))

    async def _graph_delete(self, token, url):
        self.calls.append(("delete", url, None))


@pytest.fixture
def fake_m365(monkeypatch):
    import app.services.m365 as real

    fake = _FakeM365()
    for name in ("acquire_access_token", "_graph_post", "_graph_patch", "_graph_delete"):
        monkeypatch.setattr(real, name, getattr(fake, name))
    monkeypatch.setattr(scheduling, "load_settings", AsyncMock(return_value=scheduling.Settings(m365_company_id=1)))
    record = AsyncMock()
    monkeypatch.setattr(repo, "record_sync", record)
    fake.record = record
    return fake


@pytest.mark.asyncio
async def test_sync_creates_event_in_technicians_calendar(monkeypatch, fake_m365):
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=_appointment()))
    assert await scheduling.sync_appointment(7) == "synced"
    kind, url, payload = fake_m365.calls[0]
    assert kind == "post" and url == "https://graph.microsoft.com/v1.0/users/sam@msp.example/events"
    assert payload["transactionId"] == "myportal-appointment-7"
    assert fake_m365.record.await_args.kwargs == {"sync_status": "synced", "event_id": "evt-1",
                                                  "mailbox": "sam@msp.example", "error": None}


@pytest.mark.asyncio
async def test_sync_updates_and_moves_between_calendars(monkeypatch, fake_m365):
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=_appointment(m365_event_id="evt-1",
                                                                         m365_mailbox="sam@msp.example")))
    await scheduling.sync_appointment(7)
    assert fake_m365.calls[0][0] == "patch" and fake_m365.calls[0][1].endswith("/events/evt-1")
    fake_m365.calls.clear()
    moved = _appointment(technician_email="kim@msp.example", m365_event_id="evt-1", m365_mailbox="sam@msp.example")
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=moved))
    await scheduling.sync_appointment(7)
    assert [call[0] for call in fake_m365.calls] == ["delete", "post"]
    assert "/users/sam@msp.example/events/evt-1" in fake_m365.calls[0][1]
    assert "/users/kim@msp.example/events" in fake_m365.calls[1][1]


@pytest.mark.asyncio
async def test_cancelling_removes_the_event(monkeypatch, fake_m365):
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=_appointment(status="cancelled", m365_event_id="evt-1",
                                                                         m365_mailbox="sam@msp.example")))
    assert await scheduling.sync_appointment(7) == "synced"
    assert fake_m365.calls == [("delete", "https://graph.microsoft.com/v1.0/users/sam@msp.example/events/evt-1", None)]
    assert fake_m365.record.await_args.kwargs["event_id"] is None


@pytest.mark.asyncio
async def test_failed_sync_is_recorded_with_permission_hint(monkeypatch, fake_m365):
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=_appointment()))
    fake_m365.fail = RuntimeError("Microsoft Graph POST failed (403)")
    assert await scheduling.sync_appointment(7) == "failed"
    kwargs = fake_m365.record.await_args.kwargs
    assert kwargs["sync_status"] == "failed" and "Calendars.ReadWrite" in kwargs["error"]


@pytest.mark.asyncio
async def test_sync_is_off_without_a_connection(monkeypatch):
    monkeypatch.setattr(scheduling, "load_settings", AsyncMock(return_value=scheduling.Settings()))
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=_appointment()))
    record = AsyncMock()
    monkeypatch.setattr(repo, "record_sync", record)
    assert await scheduling.sync_appointment(7) == "off"
    assert record.await_args.kwargs["sync_status"] == "off"


@pytest.mark.asyncio
async def test_save_notes_ticket_fires_event_and_syncs(monkeypatch):
    from app.repositories import tickets as tickets_repo
    from app.services import automations, tickets as tickets_service

    settings = scheduling.Settings(m365_company_id=1, timezone="Australia/Brisbane")
    monkeypatch.setattr(tickets_repo, "get_ticket", AsyncMock(return_value={"id": 42, "company_id": 5}))
    create = AsyncMock(return_value=7)
    monkeypatch.setattr(repo, "create", create)
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=_appointment()))
    reply = AsyncMock()
    monkeypatch.setattr(tickets_repo, "create_reply", reply)
    monkeypatch.setattr(tickets_service, "broadcast_ticket_event", AsyncMock())
    monkeypatch.setattr(tickets_service, "_enrich_ticket_context", AsyncMock(return_value={"id": 42}))
    handle = AsyncMock()
    monkeypatch.setattr(automations, "handle_event", handle)
    synced = []
    monkeypatch.setattr(scheduling, "schedule_sync", lambda appointment_id, remove_from=None: synced.append(appointment_id))
    values = scheduling.parse_form(_form(ticket_id="42", technician_user_id="3", kind="onsite", date="2026-12-01",
                                         start_time="10:00", duration_minutes="90"), settings, technician_ids=[3])
    await scheduling.save(values, appointment_id=None, user={"id": 1}, settings=settings)
    assert create.await_args.args[0]["company_id"] == 5 and create.await_args.kwargs["sync_status"] == "pending"
    assert reply.await_args.kwargs["is_internal"] is True and "booked" in reply.await_args.kwargs["body"]
    assert handle.await_args.args[0] == "appointments.scheduled"
    assert handle.await_args.args[1]["appointment"]["technician"] == "Sam Lee"
    assert synced == [7]


@pytest.mark.asyncio
async def test_conflicts_only_for_scheduled(monkeypatch):
    overlapping = AsyncMock(return_value=[_appointment(id=99)])
    monkeypatch.setattr(repo, "list_overlapping", overlapping)
    assert await scheduling.conflicts({"status": "cancelled"}) == []
    found = await scheduling.conflicts({"status": "scheduled", "technician_user_id": 3,
                                        "starts_at": datetime(2026, 12, 1), "ends_at": datetime(2026, 12, 1, 1)},
                                       exclude_id=7)
    assert found and overlapping.await_args.args[3] == 7
    assert "Book anyway" in scheduling.conflict_message(found, BRISBANE.zone)


def test_appointment_events_are_automation_triggers():
    from app.services import automations

    values = {event["value"] for event in automations.list_trigger_events()}
    assert set(scheduling.EVENTS.values()) <= values


@pytest.mark.asyncio
async def test_settings_route_requires_super_admin(monkeypatch):
    from fastapi.responses import RedirectResponse

    from app.features.scheduling import routes

    redirect = RedirectResponse("/login")
    monkeypatch.setattr(routes, "_main", lambda: SimpleNamespace(_require_super_admin_page=AsyncMock(return_value=(None, redirect))))
    save = AsyncMock()
    monkeypatch.setattr(repo, "save_settings", save)
    assert await routes.save_settings(SimpleNamespace()) is redirect
    save.assert_not_awaited()


def test_back_link_only_allows_admin_paths():
    from app.features.scheduling import routes

    assert routes._back({"return_to": "/admin/tickets/4"}, "/x") == "/admin/tickets/4"
    assert routes._back({"return_to": "https://evil.example/admin/"}, "/x") == "/x"
    assert routes._back({"return_to": "/admin//evil.example"}, "/x") == "/x"


def test_templates_compile_and_post_with_csrf():
    import app.main as main_module

    for name in ("scheduling/board.html", "scheduling/_appointment_editor.html", "scheduling/my_schedule.html",
                 "scheduling/_ticket_appointments.html"):
        main_module.templates.env.get_template(name)
    for name in ("scheduling/board.html", "scheduling/_appointment_editor.html"):
        source = Path("app/templates/" + name).read_text(encoding="utf-8")
        assert source.count('method="post"') == source.count('{% include "partials/csrf.html" %}'), name
        assert "<dialog" not in source and "onclick=" not in source
    ticket = Path("app/templates/admin/ticket_detail.html").read_text(encoding="utf-8")
    assert '{% include "scheduling/_ticket_appointments.html" %}' in ticket


@pytest.mark.asyncio
async def test_repository_queries_bind_every_value(monkeypatch):
    calls = []

    def record(result):
        async def capture(sql, params=None):
            calls.append((sql, tuple(params or ())))
            return result
        return capture

    monkeypatch.setattr(db, "fetch_all", record([]))
    monkeypatch.setattr(db, "fetch_one", record(None))
    monkeypatch.setattr(db, "execute", record(None))
    monkeypatch.setattr(db, "execute_rowcount", record(0))
    monkeypatch.setattr(db, "execute_returning_lastrowid", record(1))
    values = {"ticket_id": 1, "company_id": 2, "technician_user_id": 3, "kind": "onsite", "starts_at": datetime(2026, 1, 1),
              "ends_at": datetime(2026, 1, 1, 1), "location": None, "notes": None, "status": "scheduled"}
    await repo.list_between(datetime(2026, 1, 1), datetime(2026, 1, 2), technician_user_id=3)
    await repo.list_for_ticket(1)
    await repo.list_overlapping(3, datetime(2026, 1, 1), datetime(2026, 1, 2), 7)
    await repo.list_sync_due()
    await repo.create(values, user_id=1, sync_status="off")
    await repo.update(1, values, sync_status="off")
    await repo.set_status(1, "cancelled", sync_status="off")
    await repo.record_sync(1, sync_status="synced", event_id="e", mailbox="m", error=None)
    await repo.save_settings({"m365_company_id": None, "timezone": "UTC", "day_start_hour": 7, "day_end_hour": 19,
                              "default_duration_minutes": 60})
    await repo.list_unscheduled_tickets(datetime(2026, 1, 1))
    for sql, params in calls:
        assert sql.count("%s") == len(params), sql


def test_move_values_keeps_details_and_changes_time_and_technician():
    values = scheduling.move_values(_appointment(), BRISBANE, technician_ids=[3, 9], technician_user_id=9,
                                    day=date(2026, 12, 2), start_time="13:15")
    assert values["technician_user_id"] == 9
    assert values["starts_at"] == datetime(2026, 12, 2, 3, 15)  # 13:15 Brisbane (UTC+10)
    assert values["ends_at"] - values["starts_at"] == datetime(2026, 12, 1, 1, 30) - datetime(2026, 12, 1, 0, 0)
    assert (values["ticket_id"], values["kind"], values["location"], values["notes"], values["status"]) == (
        42, "onsite", "12 High St", "Bring toner", "scheduled")


def test_move_values_rejects_unknown_technician_and_finished_appointments():
    with pytest.raises(scheduling.AppointmentError) as exc:
        scheduling.move_values(_appointment(), BRISBANE, technician_ids=[3], technician_user_id=99,
                               day=date(2026, 12, 2), start_time="09:00")
    assert "technician_user_id" in exc.value.errors
    with pytest.raises(scheduling.AppointmentError) as exc:
        scheduling.move_values(_appointment(status="completed"), BRISBANE, technician_ids=[3], technician_user_id=3,
                               day=date(2026, 12, 2), start_time="09:00")
    assert "status" in exc.value.errors


@pytest.mark.asyncio
async def test_move_endpoint_saves_and_reports_conflicts(monkeypatch):
    from fastapi import HTTPException

    from app.features.scheduling import routes
    from app.services import audit as audit_service

    monkeypatch.setattr(repo, "get", AsyncMock(return_value=_appointment()))
    monkeypatch.setattr(scheduling, "load_settings", AsyncMock(return_value=BRISBANE))
    monkeypatch.setattr(routes, "_technicians", AsyncMock(return_value=[{"id": 3}, {"id": 9}]))
    monkeypatch.setattr(audit_service, "log_action", AsyncMock())
    clash = [_appointment(id=8, technician_user_id=9)]
    monkeypatch.setattr(scheduling, "conflicts", AsyncMock(return_value=clash))
    save = AsyncMock(return_value=_appointment(technician_user_id=9))
    monkeypatch.setattr(scheduling, "save", save)
    payload = routes.AppointmentMoveIn(technician_user_id=9, date=date(2026, 12, 2), start_time="13:15")

    with pytest.raises(HTTPException) as exc:
        await routes.api_move(7, payload, SimpleNamespace(), user={"id": 1})
    assert exc.value.status_code == 409 and "conflict" in exc.value.detail
    save.assert_not_awaited()

    payload.book_anyway = True
    result = await routes.api_move(7, payload, SimpleNamespace(), user={"id": 1})
    assert result["technician_user_id"] == 9
    values = save.await_args.args[0]
    assert values["starts_at"] == datetime(2026, 12, 2, 3, 15)
    assert save.await_args.kwargs["appointment_id"] == 7


def test_board_blocks_carry_move_data():
    item = scheduling.board(date(2026, 12, 1), [{"id": 3}], [_appointment()], BRISBANE)["rows"][0]["items"][0]
    assert (item["start_label"], item["duration_minutes"]) == ("10:00", 90)
    source = Path("app/templates/scheduling/board.html").read_text(encoding="utf-8")
    assert 'data-sch-move="{{ item.id }}"' in source
