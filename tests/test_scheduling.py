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
            "/api/appointments/{appointment_id}/cancel"} <= paths
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


# --------------------------------------------------------------------------- #
# Booking kinds and booking from the ticket page
# --------------------------------------------------------------------------- #

KINDS_MIGRATION = Path("migrations/584_booking_kinds.sql")


def _kind_rows():
    return [
        {"id": 1, "kind_key": "onsite", "label": "Onsite visit", "tone": "warning", "sort_order": 10, "is_default": False,
         "is_archived": False, "appointment_count": 3},
        {"id": 2, "kind_key": "workshop", "label": "Workshop", "tone": "danger", "sort_order": 20, "is_default": True,
         "is_archived": False, "appointment_count": 0},
        {"id": 3, "kind_key": "phone", "label": "Phone call", "tone": "success", "sort_order": 30, "is_default": False,
         "is_archived": True, "appointment_count": 2},
    ]


@pytest.fixture
def restore_kinds():
    yield
    scheduling.KINDS.clear()
    scheduling.KINDS.update({kind["kind_key"]: kind["label"] for kind in scheduling.DEFAULT_KINDS})
    scheduling.KIND_TONES.clear()
    scheduling.KIND_TONES.update({kind["kind_key"]: kind["tone"] for kind in scheduling.DEFAULT_KINDS})
    scheduling._kind_state.update(active=[kind["kind_key"] for kind in scheduling.DEFAULT_KINDS], default="onsite")


def test_kinds_migration_seeds_built_in_kinds_on_sqlite():
    source = KINDS_MIGRATION.read_text(encoding="utf-8")
    assert source.startswith("-- phase: expand")
    connection = sqlite3.connect(":memory:")
    for statement in db._split_sql_statements(db._adapt_sql_for_sqlite(source)):
        connection.execute(statement.replace("INSERT IGNORE INTO", "INSERT OR IGNORE INTO"))
    for statement in db._split_sql_statements(db._adapt_sql_for_sqlite(source)):  # idempotent
        connection.execute(statement.replace("INSERT IGNORE INTO", "INSERT OR IGNORE INTO"))
    rows = connection.execute("SELECT kind_key, is_default FROM booking_kinds ORDER BY sort_order").fetchall()
    assert rows == [("onsite", 1), ("remote", 0), ("phone", 0), ("meeting", 0)]


@pytest.mark.asyncio
async def test_refresh_kinds_uses_stored_kinds_and_default(monkeypatch, restore_kinds):
    monkeypatch.setattr(repo, "list_kinds", AsyncMock(return_value=_kind_rows()))
    await scheduling.refresh_kinds()
    assert scheduling.active_kinds() == {"onsite": "Onsite visit", "workshop": "Workshop"}
    assert scheduling.default_kind() == "workshop"
    assert scheduling.kind_label("phone") == "Phone call"  # archived kinds keep their label
    assert scheduling.editor_state(None, BRISBANE)["kind"] == "workshop"
    values = scheduling.parse_form(_form(ticket_id="42", technician_user_id="3", date="2026-12-01", start_time="09:00",
                                         duration_minutes="60"), BRISBANE, technician_ids=[3])
    assert values["kind"] == "workshop"
    with pytest.raises(scheduling.AppointmentError) as error:
        scheduling.parse_form(_form(ticket_id="42", technician_user_id="3", kind="unknown", date="2026-12-01",
                                    start_time="09:00", duration_minutes="60"), BRISBANE, technician_ids=[3])
    assert "kind" in error.value.errors


@pytest.mark.asyncio
async def test_refresh_kinds_falls_back_to_built_in_kinds(monkeypatch, restore_kinds):
    monkeypatch.setattr(repo, "list_kinds", AsyncMock(side_effect=RuntimeError("no table")))
    rows = await scheduling.refresh_kinds()
    assert [row["kind_key"] for row in rows] == ["onsite", "remote", "phone", "meeting"]
    assert scheduling.default_kind() == "onsite"


def test_kind_key_and_form():
    assert scheduling.kind_key_for("Workshop repair!", []) == "workshop-rep"
    assert scheduling.kind_key_for("Onsite", ["onsite", "onsite-2"]) == "onsite-3"
    assert scheduling.kind_key_for("???", []) == "kind"
    assert len(scheduling.kind_key_for("A very long booking kind name", [])) <= 16
    assert scheduling.parse_kind_form({"label": "  Bench   test ", "tone": "danger", "is_default": "1"}) == {
        "label": "Bench test", "tone": "danger", "is_default": True}
    with pytest.raises(scheduling.AppointmentError) as error:
        scheduling.parse_kind_form({"label": " ", "tone": "pink"})
    assert set(error.value.errors) == {"label", "tone"}


@pytest.mark.asyncio
async def test_remove_kind_archives_used_kinds_and_protects_the_default(monkeypatch, restore_kinds):
    from app.features.scheduling import routes

    rows = _kind_rows()
    monkeypatch.setattr(repo, "list_kinds", AsyncMock(return_value=rows))
    archive, delete = AsyncMock(), AsyncMock()
    monkeypatch.setattr(repo, "archive_kind", archive)
    monkeypatch.setattr(repo, "delete_kind", delete)
    assert await routes._remove_kind(rows[0]) == "archived"
    archive.assert_awaited_once_with(1)
    assert await routes._remove_kind({**rows[0], "appointment_count": 0}) == "deleted"
    delete.assert_awaited_once_with(1)
    with pytest.raises(scheduling.AppointmentError):
        await routes._remove_kind(rows[1])  # the default


def test_editor_offers_an_archived_kind_the_appointment_uses(restore_kinds):
    from app.features.scheduling import routes

    scheduling._kind_state.update(active=["onsite", "remote"])
    assert "phone" not in routes._editor_kinds(None)
    assert routes._editor_kinds({"kind": "phone"})["phone"] == "Phone call (archived)"


def test_kind_routes_registered():
    paths = {route.path for router in PACK.routers for route in router.routes}
    assert {"/admin/schedule/kinds", "/admin/schedule/kinds/{kind_id:int}", "/admin/schedule/kinds/{kind_id:int}/default",
            "/admin/schedule/kinds/{kind_id:int}/delete", "/api/scheduling/kinds",
            "/api/scheduling/kinds/{kind_id}"} <= paths


@pytest.mark.asyncio
async def test_kind_routes_require_super_admin(monkeypatch):
    from fastapi.responses import RedirectResponse

    from app.features.scheduling import routes

    redirect = RedirectResponse("/login")
    monkeypatch.setattr(routes, "_main", lambda: SimpleNamespace(_require_super_admin_page=AsyncMock(return_value=(None, redirect))))
    for call in (routes.create_kind(SimpleNamespace()), routes.update_kind(1, SimpleNamespace()),
                 routes.default_kind(1, SimpleNamespace()), routes.delete_kind(1, SimpleNamespace())):
        assert await call is redirect


@pytest.mark.asyncio
async def test_kind_queries_bind_every_value(monkeypatch):
    calls = []

    def record(result):
        async def capture(sql, params=None):
            calls.append((sql, tuple(params or ())))
            return result
        return capture

    monkeypatch.setattr(db, "fetch_all", record([]))
    monkeypatch.setattr(db, "fetch_one", record({"top": 40}))
    monkeypatch.setattr(db, "execute", record(None))
    monkeypatch.setattr(db, "execute_returning_lastrowid", record(5))
    await repo.list_kinds()
    await repo.get_kind(1)
    assert await repo.create_kind(kind_key="bench", label="Bench", tone="danger", is_default=True) == 5
    await repo.update_kind(1, label="x", tone="accent")
    await repo.archive_kind(1)
    await repo.delete_kind(1)
    for sql, params in calls:
        assert sql.count("%s") == len(params), sql
    assert ("INSERT INTO booking_kinds (kind_key, label, tone, sort_order, is_default, is_archived) "
            "VALUES (%s, %s, %s, %s, 0, 0)", ("bench", "Bench", "danger", 50)) in calls
    assert ("UPDATE booking_kinds SET is_default = 1, is_archived = 0 WHERE id = %s", (5,)) in calls


def _render(name, **context):
    import app.main as main_module

    template = main_module.templates.env.get_template(name)
    return template.render(csrf_token="tok", static_url=lambda path: path, **context)


def test_ticket_card_books_in_place_and_returns_to_the_ticket(restore_kinds):
    ta = {"items": [], "upcoming": 0, "timezone": "UTC", "sync_labels": scheduling.SYNC_LABELS,
          "statuses": scheduling.STATUSES, "settings": scheduling.Settings(), "day": date(2026, 12, 1),
          "technicians": [{"id": 3, "name": "Sam Lee"}], "kinds": scheduling.active_kinds(),
          "default_kind": "remote", "durations": scheduling.DURATIONS,
          "ticket": {"id": 42, "label": "#T42 Printer jam", "technician_id": 3}, "return_to": "/admin/tickets/42"}
    html = _render("scheduling/_ticket_appointments.html", ticket_appointments=ta, ticket={"id": 42, "assigned_user_id": 3})
    assert 'data-sch-open aria-haspopup="dialog" aria-controls="sch-modal"' in html
    assert 'id="sch-modal"' in html and "hidden data-sch-modal" in html.replace("  ", " ")
    assert '<input type="hidden" name="return_to" value="/admin/tickets/42">' in html
    assert '<input type="hidden" name="ticket_id" value="42">' in html
    assert 'name="kind" value="remote" checked' in html
    assert '<option value="3" selected>Sam Lee</option>' in html
    assert 'href="/admin/tickets/42" class="modal__close"' in html
    assert "/static/js/scheduling.js" in html


@pytest.mark.asyncio
async def test_save_returns_to_the_ticket(monkeypatch):
    from app.features.scheduling import routes

    user = {"id": 1}
    form = _form(ticket_id="42", technician_user_id="3", kind="onsite", date="2026-12-01", start_time="09:00",
                 duration_minutes="60", return_to="/admin/tickets/42")
    request = SimpleNamespace(form=AsyncMock(return_value=form))
    monkeypatch.setattr(routes, "_main", lambda: SimpleNamespace(_require_helpdesk_page=AsyncMock(return_value=(user, None))))
    monkeypatch.setattr(scheduling, "load_settings", AsyncMock(return_value=BRISBANE))
    monkeypatch.setattr(routes, "_technicians", AsyncMock(return_value=[{"id": 3, "first_name": "Sam"}]))
    monkeypatch.setattr(scheduling, "conflicts", AsyncMock(return_value=[]))
    monkeypatch.setattr(scheduling, "save", AsyncMock(return_value=_appointment()))
    monkeypatch.setattr(routes.audit_service, "log_action", AsyncMock())
    response = await routes.create_appointment(request)
    assert response.headers["location"].startswith("/admin/tickets/42")
