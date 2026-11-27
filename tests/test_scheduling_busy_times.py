"""Busy time imported from technicians' Microsoft 365 calendars blocks bookings on the dispatch board."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

from app.core.database import db
from app.repositories import appointments as repo
from app.services import scheduling

MIGRATIONS = (Path("migrations/582_technician_scheduling.sql"), Path("migrations/584_technician_busy_times.sql"))
BRISBANE = scheduling.Settings(m365_company_id=1, timezone="Australia/Brisbane", day_start_hour=8, day_end_hour=18)


def _event(start, end, show_as="busy", **extra):
    return {"id": extra.pop("id", "e"), "showAs": show_as, "start": {"dateTime": start, "timeZone": "UTC"},
            "end": {"dateTime": end, "timeZone": "UTC"}, **extra}


def test_migration_runs_on_sqlite():
    connection = sqlite3.connect(":memory:")
    for table in ("tickets", "companies", "users"):
        connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    for migration in MIGRATIONS:
        adapted = db._adapt_sql_for_sqlite(migration.read_text(encoding="utf-8"))
        for statement in db._split_sql_statements(adapted):
            connection.execute(statement.replace("INSERT IGNORE INTO", "INSERT OR IGNORE INTO"))
    connection.execute("INSERT INTO technician_busy_times (technician_user_id, starts_at, ends_at) "
                       "VALUES (1, '2026-01-01 00:00:00', '2026-01-01 01:00:00')")
    assert connection.execute("SELECT show_as FROM technician_busy_times").fetchone() == ("busy",)
    assert connection.execute("SELECT busy_synced_at, busy_sync_error FROM scheduling_settings").fetchone() == (None, None)
    header = MIGRATIONS[1].read_text(encoding="utf-8").splitlines()[0]
    assert header == "-- phase: expand"


def test_calendar_view_requests_only_times_never_details():
    url = scheduling.calendar_view_url("sam@msp.example", datetime(2026, 12, 1), datetime(2027, 1, 30))
    parts = urlsplit(url)
    assert parts.path == "/v1.0/users/sam@msp.example/calendarView"
    query = parse_qs(parts.query)
    assert query["startDateTime"] == ["2026-12-01T00:00:00Z"] and query["endDateTime"] == ["2027-01-30T00:00:00Z"]
    selected = set(query["$select"][0].split(","))
    assert selected == {"id", "start", "end", "showAs", "isCancelled", "transactionId"}
    assert not selected & {"subject", "body", "bodyPreview", "location", "attendees", "organizer"}


def test_busy_blocks_skip_free_cancelled_and_own_events_and_merge():
    events = [
        _event("2026-12-01T00:00:00.0000000", "2026-12-01T01:00:00.0000000", id="a"),
        _event("2026-12-01T00:30:00", "2026-12-01T02:00:00", "oof", id="b"),  # overlaps a
        _event("2026-12-01T02:00:00", "2026-12-01T02:30:00", "tentative", id="c"),  # touches b
        _event("2026-12-01T03:00:00", "2026-12-01T04:00:00", "free", id="d"),
        _event("2026-12-01T03:00:00", "2026-12-01T04:00:00", "workingElsewhere", id="e"),
        _event("2026-12-01T05:00:00", "2026-12-01T06:00:00", id="f", isCancelled=True),
        _event("2026-12-01T07:00:00", "2026-12-01T08:00:00", id="mine"),
        _event("2026-12-01T09:00:00", "2026-12-01T10:00:00", id="g", transactionId="myportal-appointment-9"),
        _event("2026-12-01T11:00:00", "2026-12-01T12:00:00", id="h"),
        _event("2026-12-01T13:00:00", "2026-12-01T12:00:00", id="backwards"),
    ]
    blocks = scheduling.busy_blocks(events, own_event_ids=["mine", None])
    assert blocks == [
        (datetime(2026, 12, 1, 0, 0), datetime(2026, 12, 1, 2, 30), "oof"),
        (datetime(2026, 12, 1, 11, 0), datetime(2026, 12, 1, 12, 0), "busy"),
    ]


def test_busy_blocks_convert_a_named_time_zone_to_utc():
    event = {"id": "x", "showAs": "busy", "start": {"dateTime": "2026-12-01T10:00:00", "timeZone": "Australia/Brisbane"},
             "end": {"dateTime": "2026-12-01T11:00:00", "timeZone": "Australia/Brisbane"}}
    assert scheduling.busy_blocks([event]) == [(datetime(2026, 12, 1, 0, 0), datetime(2026, 12, 1, 1, 0), "busy")]


class _FakeM365:
    class M365Error(Exception):
        pass

    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    async def acquire_access_token(self, company_id, *, force_client_credentials=False):
        return "token"

    async def _graph_get(self, token, url, *, extra_headers=None):
        self.requests.append((url, extra_headers))
        result = self.pages[url.split("/users/")[1].split("/")[0]]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_sync_busy_times_replaces_each_technicians_windows(monkeypatch):
    import app.services.m365 as real
    from app.repositories import chat_auto_assign

    fake = _FakeM365({
        "sam@msp.example": {"value": [_event("2026-12-01T00:00:00", "2026-12-01T01:00:00"),
                                      _event("2026-12-01T03:00:00", "2026-12-01T04:00:00", id="evt-own")]},
        "kim@msp.example": RuntimeError("Microsoft Graph GET failed (403)"),
    })
    monkeypatch.setattr(real, "acquire_access_token", fake.acquire_access_token)
    monkeypatch.setattr(real, "_graph_get", fake._graph_get)
    monkeypatch.setattr(scheduling, "load_settings", AsyncMock(return_value=BRISBANE))
    monkeypatch.setattr(chat_auto_assign, "list_all_technicians", AsyncMock(return_value=[
        {"id": 3, "email": "sam@msp.example", "first_name": "Sam", "last_name": "Lee"},
        {"id": 4, "email": "kim@msp.example", "first_name": "Kim", "last_name": "Ng"},
        {"id": 5, "email": "", "first_name": "No", "last_name": "Mail"},
    ]))
    monkeypatch.setattr(repo, "list_between", AsyncMock(return_value=[{"m365_event_id": "evt-own"}]))
    replace = AsyncMock()
    monkeypatch.setattr(repo, "replace_busy_times", replace)
    record = AsyncMock()
    monkeypatch.setattr(repo, "record_busy_sync", record)

    assert await scheduling.sync_busy_times(now=datetime(2026, 11, 30, 12, 0)) == 1
    technician_id, start, end, blocks = replace.await_args.args
    assert technician_id == 3 and start == datetime(2026, 11, 29, 12, 0) and end == datetime(2027, 1, 29, 12, 0)
    assert blocks == [(datetime(2026, 12, 1, 0, 0), datetime(2026, 12, 1, 1, 0), "busy")]
    assert fake.requests[0][1] == {"Prefer": 'outlook.timezone="UTC"'}
    error = record.await_args.kwargs["error"]
    assert error.startswith("Kim Ng:") and "Calendars.ReadWrite" in error


@pytest.mark.asyncio
async def test_sync_busy_times_does_nothing_when_sync_is_off(monkeypatch):
    monkeypatch.setattr(scheduling, "load_settings", AsyncMock(return_value=scheduling.Settings()))
    record = AsyncMock()
    monkeypatch.setattr(repo, "record_busy_sync", record)
    assert await scheduling.sync_busy_times() == 0
    record.assert_not_awaited()


def _values(**overrides):
    values = {"technician_user_id": 3, "starts_at": datetime(2026, 12, 1, 0, 0), "ends_at": datetime(2026, 12, 1, 1, 0),
              "status": "scheduled"}
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_booking_over_busy_calendar_time_is_refused_without_details(monkeypatch):
    monkeypatch.setattr(repo, "list_overlapping", AsyncMock(return_value=[]))
    busy = AsyncMock(return_value=[{"technician_user_id": 3, "starts_at": datetime(2026, 12, 1, 0, 30),
                                    "ends_at": datetime(2026, 12, 1, 2, 0), "show_as": "busy"}])
    monkeypatch.setattr(repo, "list_busy_between", busy)
    with pytest.raises(scheduling.AppointmentError) as raised:
        await scheduling.check_available(_values(), BRISBANE, technician="Sam Lee")
    message = raised.value.errors["conflict"]
    assert message.startswith("Sam Lee is unavailable then") and "10:30–12:00" in message
    assert busy.await_args.kwargs == {"technician_user_id": 3}


@pytest.mark.asyncio
async def test_booking_over_another_appointment_is_refused(monkeypatch):
    monkeypatch.setattr(repo, "list_overlapping", AsyncMock(return_value=[{
        "id": 9, "ticket_id": 42, "ticket_number": "T42", "technician_first_name": "Sam", "technician_last_name": "Lee",
        "starts_at": datetime(2026, 12, 1, 0, 0), "ends_at": datetime(2026, 12, 1, 1, 0)}]))
    busy = AsyncMock(return_value=[])
    monkeypatch.setattr(repo, "list_busy_between", busy)
    with pytest.raises(scheduling.AppointmentError) as raised:
        await scheduling.check_available(_values(), BRISBANE, appointment_id=7)
    assert "already booked" in raised.value.errors["conflict"]


@pytest.mark.asyncio
async def test_free_time_unmoved_edits_and_sync_off_are_allowed(monkeypatch):
    overlapping = AsyncMock(return_value=[])
    busy = AsyncMock(return_value=[{"starts_at": datetime(2026, 12, 1), "ends_at": datetime(2026, 12, 1, 1)}])
    monkeypatch.setattr(repo, "list_overlapping", overlapping)
    monkeypatch.setattr(repo, "list_busy_between", busy)
    # Calendar sync off: imported busy time is not consulted.
    await scheduling.check_available(_values(), scheduling.Settings(timezone="UTC"))
    busy.assert_not_awaited()
    overlapping.assert_awaited_once()  # other appointments are still checked
    overlapping.reset_mock()
    # Saving an appointment without moving it isn't re-checked.
    previous = {**_values(), "id": 7}
    await scheduling.check_available(_values(), BRISBANE, appointment_id=7, previous=previous)
    overlapping.assert_not_awaited()
    # Cancelled or completed bookings don't take time.
    await scheduling.check_available(_values(status="cancelled"), BRISBANE)
    overlapping.assert_not_awaited()


def test_board_shades_unavailable_time():
    technicians = [{"id": 3, "first_name": "Sam", "last_name": "Lee"}, {"id": 4, "first_name": "Kim"}]
    busy = [{"technician_user_id": 3, "starts_at": datetime(2026, 12, 1, 0, 0), "ends_at": datetime(2026, 12, 1, 1, 0),
             "show_as": "oof"},
            {"technician_user_id": 3, "starts_at": datetime(2026, 12, 2, 0, 0), "ends_at": datetime(2026, 12, 2, 1, 0),
             "show_as": "busy"}]
    board = scheduling.board(date(2026, 12, 1), technicians, [], BRISBANE, busy)
    sam, kim = board["rows"]
    assert kim["busy"] == []
    assert len(sam["busy"]) == 1
    band = sam["busy"][0]
    assert (band["left"], band["width"], band["start_label"], band["end_label"], band["label"]) == (20.0, 10.0, "10:00", "11:00", "Away")
    assert sam["items"] == [] and sam["booked_percent"] == 0


def test_day_unavailable_lists_local_ranges_for_the_editor():
    appointments = [
        {"id": 7, "status": "scheduled", "starts_at": datetime(2026, 12, 1, 2, 0), "ends_at": datetime(2026, 12, 1, 3, 0)},
        {"id": 8, "status": "cancelled", "starts_at": datetime(2026, 12, 1, 4, 0), "ends_at": datetime(2026, 12, 1, 5, 0)},
        {"id": 9, "status": "scheduled", "starts_at": datetime(2026, 12, 1, 6, 0), "ends_at": datetime(2026, 12, 1, 7, 0)},
    ]
    busy = [{"starts_at": datetime(2026, 11, 30, 22, 0), "ends_at": datetime(2026, 12, 1, 0, 30), "show_as": "busy"},
            {"starts_at": datetime(2026, 12, 1, 13, 0), "ends_at": datetime(2026, 12, 1, 15, 0), "show_as": "busy"}]
    ranges = scheduling.day_unavailable(date(2026, 12, 1), appointments, busy, BRISBANE, exclude_id=9)
    assert ranges == [
        {"start": "08:00", "end": "10:30", "source": "calendar"},
        {"start": "12:00", "end": "13:00", "source": "appointment"},
        {"start": "23:00", "end": "24:00", "source": "calendar"},
    ]


@pytest.mark.asyncio
async def test_busy_time_queries_bind_every_value(monkeypatch):
    calls = []

    def record(result):
        async def capture(sql, params=None):
            calls.append((sql, [tuple(params or ())]))
            return result
        return capture

    async def capture_many(sql, params_seq):
        calls.append((sql, [tuple(params) for params in params_seq]))

    monkeypatch.setattr(db, "fetch_all", record([]))
    monkeypatch.setattr(db, "fetch_one", record(None))
    monkeypatch.setattr(db, "execute", record(None))
    monkeypatch.setattr(db, "execute_many", capture_many)
    start, end = datetime(2026, 12, 1), datetime(2026, 12, 2)
    await repo.replace_busy_times(3, start, end, [(start, end, "busy"), (start, end, "oof")], synced_at=start)
    await repo.list_busy_between(start, end, technician_user_id=3)
    await repo.list_busy_between(start, end)
    await repo.clear_busy_times()
    await repo.get_busy_sync_state()
    await repo.record_busy_sync(synced_at=start, error="x" * 900)
    assert len(calls) == 7
    for sql, param_sets in calls:
        for params in param_sets:
            assert sql.count("%s") == len(params), sql
    assert len(calls[-1][1][0][1]) == 500


def test_routes_register_availability_and_drop_book_anyway():
    from app.features.scheduling import PACK, routes

    paths = {route.path for router in PACK.routers for route in router.routes}
    assert "/api/scheduling/availability" in paths
    assert "book_anyway" not in routes.AppointmentIn.model_fields
    editor = Path("app/templates/scheduling/_appointment_editor.html").read_text(encoding="utf-8")
    assert "book_anyway" not in editor and "data-sch-avail" in editor
    board = Path("app/templates/scheduling/board.html").read_text(encoding="utf-8")
    assert "data-sch-busy" in board


@pytest.mark.asyncio
async def test_putting_a_cancelled_booking_back_over_busy_time_is_refused(monkeypatch):
    from types import SimpleNamespace

    from app.features.scheduling import routes

    record = {"id": 7, "ticket_id": 42, "technician_user_id": 3, "technician_first_name": "Sam", "status": "cancelled",
              "starts_at": datetime(2026, 12, 1, 0, 0), "ends_at": datetime(2026, 12, 1, 1, 0)}
    monkeypatch.setattr(routes, "_main", lambda: SimpleNamespace(
        _require_helpdesk_page=AsyncMock(return_value=({"id": 1}, None))))
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=record))
    monkeypatch.setattr(scheduling, "load_settings", AsyncMock(return_value=BRISBANE))
    monkeypatch.setattr(repo, "list_overlapping", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "list_busy_between", AsyncMock(return_value=[
        {"starts_at": datetime(2026, 12, 1, 0, 0), "ends_at": datetime(2026, 12, 1, 2, 0)}]))
    set_status = AsyncMock()
    monkeypatch.setattr(scheduling, "set_status", set_status)
    flashed = []
    monkeypatch.setattr(routes, "flash_redirect", lambda url, message, kind: flashed.append((url, message, kind)) or "r")

    async def form():
        return {"status": "scheduled"}

    assert await routes.appointment_status(7, SimpleNamespace(form=form)) == "r"
    set_status.assert_not_awaited()
    assert flashed[0][2] == "error" and "unavailable" in flashed[0][1]
