"""Routes for technician scheduling and dispatch (:mod:`app.services.scheduling`)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from datetime import date as DateType
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.datastructures import ImmutableMultiDict

from app.api.dependencies.auth import require_helpdesk_technician, require_super_admin
from app.repositories import appointments as repo
from app.repositories import company_memberships as membership_repo
from app.repositories import companies as company_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import scheduling

router = APIRouter(tags=["Scheduling"])

PAGE_PATH = "/admin/schedule"


def _main():
    from app import main as main_module

    return main_module


def _parse_day(value: str | None, settings: scheduling.Settings) -> date:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return datetime.now(settings.zone).date()


def _optional_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# The role "Technician" switch (``menu.admin.technician``) grants this
# permission. ``helpdesk.technician`` is not used: any role with ticket access
# carries it, so it would put customer users on the dispatch board.
TECHNICIAN_PERMISSION = "company.switch_all"


async def _technicians() -> list[dict[str, Any]]:
    """Return Super Admins and users whose active role has the Technician switch on."""
    users = await membership_repo.list_users_with_permission(TECHNICIAN_PERMISSION)
    return sorted(users, key=lambda user: scheduling.technician_name(user).casefold())


def _back(form: Any, fallback: str) -> str:
    value = str(form.get("return_to") or "")
    return value if value.startswith("/admin/") and "//" not in value else fallback


def _audit(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value.isoformat() if isinstance(value, (datetime, date)) else value for key, value in values.items()}


def _editor_kinds(editing: dict[str, Any] | None) -> dict[str, str]:
    """Kinds offered in the booking editor, plus an archived kind the appointment already uses."""

    kinds = scheduling.active_kinds()
    current = str((editing or {}).get("kind") or "")
    if current and current not in kinds and current in scheduling.KINDS:
        kinds[current] = scheduling.KINDS[current] + " (archived)"
    return kinds


def _kind_json(kind: dict[str, Any]) -> dict[str, Any]:
    return {"id": kind.get("id"), "key": kind["kind_key"], "label": kind["label"], "tone": kind.get("tone") or "accent",
            "is_default": bool(kind.get("is_default")), "is_archived": bool(kind.get("is_archived")),
            "appointment_count": int(kind.get("appointment_count") or 0)}


async def _render_board(request: Request, user: dict[str, Any], *, day: date, editing: dict[str, Any] | None = None,
                        errors: dict[str, str] | None = None, status_code: int = status.HTTP_200_OK,
                        kinds_open: bool = False, kind_editing: dict[str, Any] | None = None,
                        kind_errors: dict[str, str] | None = None):
    settings = await scheduling.load_settings()
    booking_kinds = [_kind_json(kind) for kind in await scheduling.refresh_kinds()]
    technicians = await _technicians()
    start, end = scheduling.day_window(day, settings)
    appointments = await repo.list_between(start, end)
    busy = await repo.list_busy_between(start, end) if settings.calendar_sync else []
    board = scheduling.board(day, technicians, appointments, settings, busy)
    busy_sync = await repo.get_busy_sync_state() if settings.calendar_sync else {}
    unscheduled = await repo.list_unscheduled_tickets(datetime.now(timezone.utc).replace(tzinfo=None))
    from app.services import business_hours as business_hours_service

    response = await _main()._render_template(
        "scheduling/board.html",
        request,
        user,
        extra={
            "title": "Schedule",
            "board": board,
            "day": day,
            "previous_day": (day - timedelta(days=1)).isoformat(),
            "next_day": (day + timedelta(days=1)).isoformat(),
            "today": datetime.now(settings.zone).date().isoformat(),
            "settings": settings,
            "technicians": [{"id": tech["id"], "name": scheduling.technician_name(tech)} for tech in technicians],
            "unscheduled": unscheduled,
            "kinds": _editor_kinds(editing),
            "legend_kinds": [kind for kind in booking_kinds if not kind["is_archived"]],
            "default_kind": scheduling.default_kind(),
            "booking_kinds": booking_kinds,
            "tones": scheduling.TONES,
            "kinds_open": kinds_open,
            "kind_editing": kind_editing,
            "kind_errors": kind_errors or {},
            "statuses": scheduling.STATUSES,
            "durations": scheduling.DURATIONS,
            "sync_labels": scheduling.SYNC_LABELS,
            "editing": editing,
            "errors": errors or {},
            "busy_sync": busy_sync,
            "sync_interval_minutes": scheduling.SYNC_INTERVAL_SECONDS // 60,
            "can_manage": bool(user.get("is_super_admin")),
            "companies": await company_repo.list_companies() if user.get("is_super_admin") else [],
            "timezones": business_hours_service.timezone_options() if user.get("is_super_admin") else [],
        },
    )
    response.status_code = status_code
    return response


@router.get(PAGE_PATH, response_class=HTMLResponse, summary="Dispatch board")
async def board_page(
    request: Request,
    day: str | None = Query(default=None, alias="date"),
    appointment: int | None = Query(default=None, ge=1),
    new: bool = Query(default=False),
    ticket_id: int | None = Query(default=None, ge=1),
    technician_id: int | None = Query(default=None, ge=1),
    start: str | None = Query(default=None, max_length=5),
    kinds: bool = Query(default=False, description="Open the booking kinds list (super admins)"),
    kind: str | None = Query(default=None, max_length=12, description="'new' or a booking kind's ID to edit"),
    return_to: str | None = Query(default=None, max_length=200, description="Where to go once the booking is made"),
):
    user, redirect = await _main()._require_helpdesk_page(request)
    if redirect:
        return redirect
    settings = await scheduling.load_settings()
    selected = _parse_day(day, settings)
    kind_editing = None
    if kind and user.get("is_super_admin"):
        if kind == "new":
            kind_editing = {"id": None, "label": "", "tone": "accent", "is_default": False, "appointment_count": 0}
        else:
            record = await repo.get_kind(_optional_int(kind) or 0)
            if not record:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking kind not found")
            kind_editing = _kind_json(record)
    if kind_editing is not None or kinds:
        return await _render_board(request, user, day=selected, kinds_open=bool(kinds and user.get("is_super_admin")),
                                   kind_editing=kind_editing)
    editing = None
    if appointment is not None:
        record = await repo.get(appointment)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
        editing = scheduling.editor_state(record, settings)
        editing["sync_status"] = record.get("sync_status")
        editing["sync_error"] = record.get("sync_error")
        selected = date.fromisoformat(editing["date"]) if day is None else selected
    elif new:
        editing = scheduling.editor_state(None, settings, ticket_id=ticket_id,
                                          technician_id=technician_id or int(user["id"]), day=selected, start=start)
        if ticket_id:
            from app.repositories import tickets as tickets_repo

            ticket = await tickets_repo.get_ticket(ticket_id)
            if ticket:
                editing["ticket_label"] = f"#{ticket.get('ticket_number') or ticket_id} {ticket.get('subject') or ''}"
        editing["return_to"] = _back({"return_to": return_to}, "") or None
    return await _render_board(request, user, day=selected, editing=editing)


@router.get(PAGE_PATH + "/me", response_class=HTMLResponse, summary="A technician's upcoming appointments")
async def my_schedule(request: Request, technician_id: int | None = Query(default=None, ge=1),
                      week: str | None = Query(default=None)):
    user, redirect = await _main()._require_helpdesk_page(request)
    if redirect:
        return redirect
    settings = await scheduling.load_settings()
    start_day = _parse_day(week, settings)
    start_day -= timedelta(days=start_day.weekday())
    technician = technician_id or int(user["id"])
    window_start, _ = scheduling.day_window(start_day, settings)
    window_end, _ = scheduling.day_window(start_day + timedelta(days=7), settings)
    appointments = await repo.list_between(window_start, window_end, technician_user_id=technician)
    technicians = await _technicians()
    return await _main()._render_template(
        "scheduling/my_schedule.html",
        request,
        user,
        extra={
            "title": "My schedule",
            "days": scheduling.group_by_day(appointments, settings.zone),
            "week_start": start_day,
            "week_end": start_day + timedelta(days=6),
            # Calendar dates in the scheduling time zone, so they are labelled here.
            "week_heading": f"Week of {start_day:%d %B %Y}",
            "week_range": f"{start_day:%a %d %b} – {start_day + timedelta(days=6):%a %d %b}",
            "previous_week": (start_day - timedelta(days=7)).isoformat(),
            "next_week": (start_day + timedelta(days=7)).isoformat(),
            "technician_id": technician,
            "technicians": [{"id": tech["id"], "name": scheduling.technician_name(tech)} for tech in technicians],
            "settings": settings,
            "sync_labels": scheduling.SYNC_LABELS,
            "statuses": scheduling.STATUSES,
        },
    )


async def _check_available(values: dict[str, Any], settings: scheduling.Settings, appointment_id: int | None,
                           technicians: list[dict[str, Any]]) -> None:
    previous = await repo.get(appointment_id) if appointment_id is not None else None
    technician = next((tech for tech in technicians if int(tech["id"]) == int(values["technician_user_id"])), None)
    await scheduling.check_available(values, settings, appointment_id=appointment_id, previous=previous,
                                     technician=scheduling.technician_name(technician) if technician else None)


async def _save(request: Request, appointment_id: int | None):
    user, redirect = await _main()._require_helpdesk_page(request)
    if redirect:
        return redirect
    settings = await scheduling.load_settings()
    if appointment_id is not None and not await repo.get(appointment_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    form = await request.form()
    technicians = await _technicians()
    posted = {key: form.get(key) for key in ("ticket_id", "technician_user_id", "kind", "date", "start_time",
                                              "duration_minutes", "location", "notes", "status")}
    posted.update(id=appointment_id, ticket_id=_optional_int(posted["ticket_id"]),
                  technician_user_id=_optional_int(posted["technician_user_id"]),
                  duration_minutes=_optional_int(posted["duration_minutes"]), return_to=_back(form, ""))
    day = _parse_day(str(form.get("date") or ""), settings)
    try:
        values = scheduling.parse_form(form, settings, technician_ids=[int(tech["id"]) for tech in technicians])
        await _check_available(values, settings, appointment_id, technicians)
        saved = await scheduling.save(values, appointment_id=appointment_id, user=user, settings=settings)
    except scheduling.AppointmentError as exc:
        return await _render_board(request, user, day=day, editing=posted, errors=exc.errors,
                                   status_code=status.HTTP_400_BAD_REQUEST)
    await audit_service.log_action(action="appointment.save", user_id=user.get("id"), entity_type="appointment",
                                   entity_id=int(saved["id"]), new_value=_audit(values), request=request)
    when = scheduling.describe_when(saved, settings.zone)
    message = f"Booked {scheduling.technician_name(saved)} for {when}." if appointment_id is None else f"Saved: {when}."
    return flash_redirect(_back(form, f"{PAGE_PATH}?date={day.isoformat()}"), message, "success")


@router.post(PAGE_PATH + "/appointments", response_class=HTMLResponse, summary="Book an appointment")
async def create_appointment(request: Request):
    return await _save(request, None)


@router.post(PAGE_PATH + "/appointments/{appointment_id:int}", response_class=HTMLResponse,
             summary="Update an appointment")
async def update_appointment(appointment_id: int, request: Request):
    return await _save(request, appointment_id)


@router.post(PAGE_PATH + "/appointments/{appointment_id:int}/status", response_class=HTMLResponse,
             summary="Complete or cancel an appointment")
async def appointment_status(appointment_id: int, request: Request):
    user, redirect = await _main()._require_helpdesk_page(request)
    if redirect:
        return redirect
    record = await repo.get(appointment_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    form = await request.form()
    new_status = str(form.get("status") or "")
    settings = await scheduling.load_settings()
    if new_status == "scheduled" and record["status"] != "scheduled":
        try:  # putting a cancelled booking back takes its time again
            await scheduling.check_available({**record, "status": "scheduled"}, settings, appointment_id=appointment_id,
                                             technician=scheduling.technician_name(record))
        except scheduling.AppointmentError as exc:
            return flash_redirect(_back(form, f"{PAGE_PATH}?appointment={appointment_id}"),
                                  exc.errors["conflict"], "error")
    try:
        await scheduling.set_status(appointment_id, new_status, user=user, settings=settings)
    except scheduling.AppointmentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await audit_service.log_action(action=f"appointment.{new_status}", user_id=user.get("id"),
                                   entity_type="appointment", entity_id=appointment_id, request=request)
    day = scheduling.to_local(record["starts_at"], settings.zone).date().isoformat()
    label = {"cancelled": "Cancelled", "completed": "Marked as done", "scheduled": "Back on the schedule"}[new_status]
    return flash_redirect(_back(form, f"{PAGE_PATH}?date={day}"), f"{label}.", "success")


@router.post(PAGE_PATH + "/appointments/{appointment_id:int}/sync", response_class=HTMLResponse,
             summary="Retry writing an appointment to the technician's calendar")
async def retry_sync(appointment_id: int, request: Request):
    user, redirect = await _main()._require_helpdesk_page(request)
    if redirect:
        return redirect
    if not await repo.get(appointment_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    form = await request.form()
    outcome = await scheduling.sync_appointment(appointment_id)
    messages = {"synced": ("Written to the technician's calendar.", "success"),
                "off": ("Calendar sync is off. Choose a Microsoft 365 connection in the settings.", "warning"),
                "failed": ("Calendar sync failed again. See the error on the appointment.", "error")}
    message, kind = messages.get(outcome, ("Nothing to sync.", "info"))
    return flash_redirect(_back(form, f"{PAGE_PATH}?appointment={appointment_id}"), message, kind)


@router.post(PAGE_PATH + "/settings", response_class=HTMLResponse, summary="Scheduling settings")
async def save_settings(request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        values = scheduling.parse_settings_form(form)
    except scheduling.AppointmentError as exc:
        return flash_redirect(PAGE_PATH + "#schedule-settings", " ".join(exc.errors.values()), "error")
    if values["m365_company_id"] and not await company_repo.get_company_by_id(values["m365_company_id"]):
        return flash_redirect(PAGE_PATH + "#schedule-settings", "Choose a company from the list.", "error")
    await repo.save_settings(values)
    if not values["m365_company_id"]:
        await repo.clear_busy_times()  # stale once calendars are no longer read
    await audit_service.log_action(action="scheduling.settings", user_id=user.get("id"), entity_type="scheduling_settings",
                                   entity_id=1, new_value=values, request=request)
    message = ("Saved. New and changed appointments will be written to technicians' calendars."
               if values["m365_company_id"] else "Saved. Calendar sync is off.")
    return flash_redirect(PAGE_PATH + "#schedule-settings", message, "success")


# --------------------------------------------------------------------------- #
# Booking kinds (super admins)
# --------------------------------------------------------------------------- #

KINDS_PATH = PAGE_PATH + "?kinds=1"


async def _save_kind(request: Request, kind_id: int | None):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    existing = await repo.get_kind(kind_id) if kind_id is not None else None
    if kind_id is not None and not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking kind not found")
    form = await request.form()
    try:
        values = scheduling.parse_kind_form(form)
    except scheduling.AppointmentError as exc:
        posted = {"id": kind_id, "label": str(form.get("label") or ""), "tone": str(form.get("tone") or "accent"),
                  "is_default": bool(form.get("is_default")),
                  "appointment_count": (existing or {}).get("appointment_count", 0)}
        settings = await scheduling.load_settings()
        return await _render_board(request, user, day=datetime.now(settings.zone).date(), kind_editing=posted,
                                   kind_errors=exc.errors, status_code=status.HTTP_400_BAD_REQUEST)
    saved_id = await _store_kind(values, kind_id, existing)
    await audit_service.log_action(action="scheduling.kind.save", user_id=user.get("id"), entity_type="booking_kind",
                                   entity_id=saved_id, new_value=values, request=request)
    return flash_redirect(KINDS_PATH, f"Saved “{values['label']}”.", "success")


async def _store_kind(values: dict[str, Any], kind_id: int | None, existing: dict[str, Any] | None) -> int:
    if kind_id is None:
        taken = [kind["kind_key"] for kind in await repo.list_kinds()]
        kind_id = await repo.create_kind(kind_key=scheduling.kind_key_for(values["label"], taken), label=values["label"],
                                         tone=values["tone"], is_default=values["is_default"])
    else:
        await repo.update_kind(kind_id, label=values["label"], tone=values["tone"])
        if values["is_default"] and not (existing or {}).get("is_default"):
            await repo.set_default_kind(kind_id)
    await scheduling.refresh_kinds()
    return kind_id


async def _remove_kind(kind: dict[str, Any]) -> str:
    """Delete an unused kind, or archive one that appointments use. Returns "deleted" or "archived"."""

    if kind["is_default"]:
        raise scheduling.AppointmentError({"kind": "Choose another default booking kind before removing this one."})
    active = [item for item in await repo.list_kinds() if not item["is_archived"]]
    if not kind["is_archived"] and len(active) <= 1:
        raise scheduling.AppointmentError({"kind": "Keep at least one booking kind."})
    if kind["appointment_count"]:
        await repo.archive_kind(int(kind["id"]))
        outcome = "archived"
    else:
        await repo.delete_kind(int(kind["id"]))
        outcome = "deleted"
    await scheduling.refresh_kinds()
    return outcome


@router.post(PAGE_PATH + "/kinds", response_class=HTMLResponse, summary="Add a booking kind")
async def create_kind(request: Request):
    return await _save_kind(request, None)


@router.post(PAGE_PATH + "/kinds/{kind_id:int}", response_class=HTMLResponse, summary="Update a booking kind")
async def update_kind(kind_id: int, request: Request):
    return await _save_kind(request, kind_id)


@router.post(PAGE_PATH + "/kinds/{kind_id:int}/default", response_class=HTMLResponse,
             summary="Make a booking kind the default for new bookings")
async def default_kind(kind_id: int, request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    kind = await repo.get_kind(kind_id)
    if not kind:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking kind not found")
    await repo.set_default_kind(kind_id)
    await scheduling.refresh_kinds()
    await audit_service.log_action(action="scheduling.kind.default", user_id=user.get("id"), entity_type="booking_kind",
                                   entity_id=kind_id, request=request)
    form = await request.form()
    return flash_redirect(_back(form, KINDS_PATH), f"New bookings now start as “{kind['label']}”.", "success")


@router.post(PAGE_PATH + "/kinds/{kind_id:int}/delete", response_class=HTMLResponse, summary="Delete a booking kind")
async def delete_kind(kind_id: int, request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    kind = await repo.get_kind(kind_id)
    if not kind:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking kind not found")
    try:
        outcome = await _remove_kind(kind)
    except scheduling.AppointmentError as exc:
        return flash_redirect(KINDS_PATH, str(exc), "error")
    await audit_service.log_action(action=f"scheduling.kind.{outcome}", user_id=user.get("id"),
                                   entity_type="booking_kind", entity_id=kind_id, request=request)
    message = (f"Deleted “{kind['label']}”." if outcome == "deleted" else
               f"Archived “{kind['label']}”: its {kind['appointment_count']} appointment(s) keep it, "
               "but it is no longer offered for new bookings.")
    return flash_redirect(KINDS_PATH, message, "success")


async def ticket_appointments_context(ticket_id: int) -> dict[str, Any]:
    """The ticket page's Appointments card and its booking editor."""

    from app.repositories import tickets as tickets_repo

    settings = await scheduling.load_settings()
    items = []
    for appointment in await repo.list_for_ticket(ticket_id):
        items.append({**appointment, "when": scheduling.describe_when(appointment, settings.zone),
                      "kind_label": scheduling.KINDS.get(appointment["kind"], "Appointment"),
                      "technician": scheduling.technician_name(appointment),
                      "day": scheduling.to_local(appointment["starts_at"], settings.zone).date().isoformat()})
    upcoming = [item for item in items if item["status"] == "scheduled"]
    ticket = await tickets_repo.get_ticket(ticket_id) or {}
    technicians = await _technicians()
    technician_ids = {int(tech["id"]) for tech in technicians}
    assigned = _optional_int(ticket.get("assigned_user_id"))
    return {"items": items, "upcoming": len(upcoming), "timezone": settings.timezone,
            "sync_labels": scheduling.SYNC_LABELS, "statuses": scheduling.STATUSES,
            # The booking editor, opened in place on the ticket page.
            "settings": settings, "day": datetime.now(settings.zone).date(),
            "technicians": [{"id": tech["id"], "name": scheduling.technician_name(tech)} for tech in technicians],
            "kinds": scheduling.active_kinds(), "default_kind": scheduling.default_kind(),
            "durations": scheduling.DURATIONS,
            "ticket": {"id": ticket_id,
                       "label": f"#{ticket.get('ticket_number') or ticket_id} {ticket.get('subject') or ''}".strip(),
                       "technician_id": assigned if assigned in technician_ids else None},
            "return_to": f"/admin/tickets/{ticket_id}"}


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


class AppointmentIn(BaseModel):
    ticket_id: int
    technician_user_id: int
    kind: str | None = Field(default=None, description="A booking kind's key (GET /api/scheduling/kinds); "
                                                    "the default kind when left out")
    date: DateType = Field(description="Local date in the scheduling time zone")
    start_time: str = Field(description="Local start time, HH:MM", pattern=r"^\d{2}:\d{2}$")
    duration_minutes: int = Field(ge=5, le=scheduling.MAX_DURATION_MINUTES)
    location: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=4000)
    status: str = Field(default="scheduled", description="scheduled, completed or cancelled")


def _api_appointment(appointment: dict[str, Any], settings: scheduling.Settings) -> dict[str, Any]:
    def iso(value: Any) -> Any:
        return value.isoformat() + "Z" if isinstance(value, datetime) else value

    return {
        "id": appointment["id"], "ticket_id": appointment["ticket_id"], "ticket_number": appointment.get("ticket_number"),
        "ticket_subject": appointment.get("ticket_subject"), "company_id": appointment.get("company_id"),
        "company_name": appointment.get("company_name"), "technician_user_id": appointment["technician_user_id"],
        "technician": scheduling.technician_name(appointment), "kind": appointment["kind"],
        "starts_at": iso(appointment["starts_at"]), "ends_at": iso(appointment["ends_at"]),
        "when": scheduling.describe_when(appointment, settings.zone), "location": appointment.get("location"),
        "notes": appointment.get("notes"), "status": appointment["status"],
        "calendar": {"status": appointment.get("sync_status"), "error": appointment.get("sync_error")},
    }


@router.get("/api/appointments", summary="Appointments between two times")
async def api_list(
    start: datetime = Query(description="UTC, ISO 8601"),
    end: datetime = Query(description="UTC, ISO 8601"),
    technician_id: int | None = Query(default=None),
    include_cancelled: bool = Query(default=False),
    _: dict = Depends(require_helpdesk_technician),
) -> list[dict[str, Any]]:
    settings = await scheduling.load_settings()
    start_utc, end_utc = scheduling._naive_utc(start), scheduling._naive_utc(end)
    if end_utc <= start_utc or end_utc - start_utc > timedelta(days=93):
        raise HTTPException(status_code=422, detail="Choose a range of up to 93 days.")
    items = await repo.list_between(start_utc, end_utc, technician_user_id=technician_id,
                                    include_cancelled=include_cancelled)
    return [_api_appointment(item, settings) for item in items]


@router.get("/api/appointments/{appointment_id}", summary="An appointment")
async def api_get(appointment_id: int, _: dict = Depends(require_helpdesk_technician)) -> dict[str, Any]:
    record = await repo.get(appointment_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    return _api_appointment(record, await scheduling.load_settings())


@router.get("/api/tickets/{ticket_id}/appointments", summary="A ticket's appointments")
async def api_ticket_appointments(ticket_id: int, _: dict = Depends(require_helpdesk_technician)) -> list[dict[str, Any]]:
    settings = await scheduling.load_settings()
    return [_api_appointment(item, settings) for item in await repo.list_for_ticket(ticket_id)]


async def _api_save(payload: AppointmentIn, request: Request, user: dict, appointment_id: int | None) -> dict[str, Any]:
    settings = await scheduling.load_settings()
    if appointment_id is not None and not await repo.get(appointment_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    form = ImmutableMultiDict([(key, value.isoformat() if isinstance(value, date) else str(value))
                               for key, value in payload.model_dump().items() if value is not None])
    technicians = await _technicians()
    try:
        values = scheduling.parse_form(form, settings, technician_ids=[int(tech["id"]) for tech in technicians])
        await _check_available(values, settings, appointment_id, technicians)
        saved = await scheduling.save(values, appointment_id=appointment_id, user=user, settings=settings)
    except scheduling.AppointmentError as exc:
        raise HTTPException(status_code=409 if "conflict" in exc.errors else 422, detail=exc.errors) from exc
    await audit_service.log_action(action="appointment.save", user_id=user.get("id"), entity_type="appointment",
                                   entity_id=int(saved["id"]), new_value=_audit(values), request=request)
    return _api_appointment(saved, settings)


@router.post("/api/appointments", summary="Book an appointment", status_code=status.HTTP_201_CREATED)
async def api_create(payload: AppointmentIn, request: Request,
                     user: dict = Depends(require_helpdesk_technician)) -> dict[str, Any]:
    return await _api_save(payload, request, user, None)


@router.put("/api/appointments/{appointment_id}", summary="Update an appointment")
async def api_update(appointment_id: int, payload: AppointmentIn, request: Request,
                     user: dict = Depends(require_helpdesk_technician)) -> dict[str, Any]:
    return await _api_save(payload, request, user, appointment_id)


class AppointmentMoveIn(BaseModel):
    technician_user_id: int = Field(description="Technician to move the appointment to")
    date: DateType = Field(description="New local date in the scheduling time zone")
    start_time: str = Field(description="New local start time, HH:MM", pattern=r"^\d{2}:\d{2}$")


@router.post("/api/appointments/{appointment_id}/move", summary="Move an appointment to another time or technician")
async def api_move(appointment_id: int, payload: AppointmentMoveIn, request: Request,
                   user: dict = Depends(require_helpdesk_technician)) -> dict[str, Any]:
    """Used by dragging a booking on the dispatch board. The length and details stay the same.

    Refused (409) over another of the technician's appointments or their busy calendar time.
    """

    record = await repo.get(appointment_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    settings = await scheduling.load_settings()
    technicians = await _technicians()
    try:
        values = scheduling.move_values(record, settings, technician_ids=[int(tech["id"]) for tech in technicians],
                                        technician_user_id=payload.technician_user_id, day=payload.date,
                                        start_time=payload.start_time)
        await _check_available(values, settings, appointment_id, technicians)
        saved = await scheduling.save(values, appointment_id=appointment_id, user=user, settings=settings)
    except scheduling.AppointmentError as exc:
        raise HTTPException(status_code=409 if "conflict" in exc.errors else 422, detail=exc.errors) from exc
    await audit_service.log_action(action="appointment.move", user_id=user.get("id"), entity_type="appointment",
                                   entity_id=appointment_id, previous_value=_audit(
                                       {key: record.get(key) for key in ("technician_user_id", "starts_at", "ends_at")}),
                                   new_value=_audit(values), request=request)
    return _api_appointment(saved, settings)


@router.post("/api/appointments/{appointment_id}/cancel", summary="Cancel an appointment")
async def api_cancel(appointment_id: int, request: Request,
                     user: dict = Depends(require_helpdesk_technician)) -> dict[str, Any]:
    if not await repo.get(appointment_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    settings = await scheduling.load_settings()
    saved = await scheduling.set_status(appointment_id, "cancelled", user=user, settings=settings)
    await audit_service.log_action(action="appointment.cancelled", user_id=user.get("id"), entity_type="appointment",
                                   entity_id=appointment_id, request=request)
    return _api_appointment(saved or {}, settings)


class BookingKindIn(BaseModel):
    label: str = Field(min_length=1, max_length=scheduling.MAX_KIND_LABEL)
    tone: str = Field(default="accent", description="accent, warning, success, danger or neutral")
    is_default: bool = Field(default=False, description="Start new bookings with this kind")


@router.get("/api/scheduling/kinds", summary="Booking kinds")
async def api_list_kinds(_: dict = Depends(require_helpdesk_technician)) -> list[dict[str, Any]]:
    return [_kind_json(kind) for kind in await scheduling.refresh_kinds()]


@router.post("/api/scheduling/kinds", summary="Add a booking kind", status_code=status.HTTP_201_CREATED)
async def api_create_kind(payload: BookingKindIn, request: Request,
                          user: dict = Depends(require_super_admin)) -> dict[str, Any]:
    return await _api_save_kind(payload, request, user, None)


@router.put("/api/scheduling/kinds/{kind_id}", summary="Update a booking kind")
async def api_update_kind(kind_id: int, payload: BookingKindIn, request: Request,
                          user: dict = Depends(require_super_admin)) -> dict[str, Any]:
    return await _api_save_kind(payload, request, user, kind_id)


async def _api_save_kind(payload: BookingKindIn, request: Request, user: dict, kind_id: int | None) -> dict[str, Any]:
    existing = await repo.get_kind(kind_id) if kind_id is not None else None
    if kind_id is not None and not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking kind not found")
    try:
        values = scheduling.parse_kind_form(payload.model_dump())
    except scheduling.AppointmentError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    saved_id = await _store_kind(values, kind_id, existing)
    await audit_service.log_action(action="scheduling.kind.save", user_id=user.get("id"), entity_type="booking_kind",
                                   entity_id=saved_id, new_value=values, request=request)
    return _kind_json(await repo.get_kind(saved_id) or {})


@router.delete("/api/scheduling/kinds/{kind_id}", summary="Delete (or archive, if in use) a booking kind")
async def api_delete_kind(kind_id: int, request: Request, user: dict = Depends(require_super_admin)) -> dict[str, Any]:
    kind = await repo.get_kind(kind_id)
    if not kind:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking kind not found")
    try:
        outcome = await _remove_kind(kind)
    except scheduling.AppointmentError as exc:
        raise HTTPException(status_code=409, detail=exc.errors) from exc
    await audit_service.log_action(action=f"scheduling.kind.{outcome}", user_id=user.get("id"),
                                   entity_type="booking_kind", entity_id=kind_id, request=request)
    return {"id": kind_id, "outcome": outcome}


@router.get("/api/scheduling/availability", summary="When a technician is unavailable on a day")
async def api_availability(
    technician_id: int = Query(ge=1),
    day: DateType = Query(alias="date", description="Local date in the scheduling time zone"),
    exclude: int | None = Query(default=None, ge=1, description="An appointment being moved, left out"),
    _: dict = Depends(require_helpdesk_technician),
) -> dict[str, Any]:
    """Taken time as local HH:MM ranges. Calendar time shows only when, never what."""

    settings = await scheduling.load_settings()
    start, end = scheduling.day_window(day, settings)
    appointments = await repo.list_between(start, end, technician_user_id=technician_id)
    busy = await repo.list_busy_between(start, end, technician_user_id=technician_id) if settings.calendar_sync else []
    return {"date": day.isoformat(), "timezone": settings.timezone, "technician_id": technician_id,
            "unavailable": scheduling.day_unavailable(day, appointments, busy, settings, exclude_id=exclude)}


@router.get("/api/scheduling/settings", summary="Scheduling settings")
async def api_settings(_: dict = Depends(require_super_admin)) -> dict[str, Any]:
    settings = await scheduling.load_settings()
    return {"m365_company_id": settings.m365_company_id, "timezone": settings.timezone,
            "day_start_hour": settings.day_start_hour, "day_end_hour": settings.day_end_hour,
            "default_duration_minutes": settings.default_duration_minutes}


__all__ = ["router", "ticket_appointments_context"]
