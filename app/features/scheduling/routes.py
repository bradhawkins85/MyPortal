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


async def _render_board(request: Request, user: dict[str, Any], *, day: date, editing: dict[str, Any] | None = None,
                        errors: dict[str, str] | None = None, status_code: int = status.HTTP_200_OK):
    settings = await scheduling.load_settings()
    technicians = await _technicians()
    start, end = scheduling.day_window(day, settings)
    appointments = await repo.list_between(start, end)
    board = scheduling.board(day, technicians, appointments, settings)
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
            "kinds": scheduling.KINDS,
            "statuses": scheduling.STATUSES,
            "durations": scheduling.DURATIONS,
            "sync_labels": scheduling.SYNC_LABELS,
            "editing": editing,
            "errors": errors or {},
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
):
    user, redirect = await _main()._require_helpdesk_page(request)
    if redirect:
        return redirect
    settings = await scheduling.load_settings()
    selected = _parse_day(day, settings)
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
                  duration_minutes=_optional_int(posted["duration_minutes"]), book_anyway=bool(form.get("book_anyway")))
    day = _parse_day(str(form.get("date") or ""), settings)
    try:
        values = scheduling.parse_form(form, settings, technician_ids=[int(tech["id"]) for tech in technicians])
        overlapping = await scheduling.conflicts(values, exclude_id=appointment_id)
        if overlapping and not form.get("book_anyway"):
            raise scheduling.AppointmentError({"conflict": scheduling.conflict_message(overlapping, settings.zone)})
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
    await audit_service.log_action(action="scheduling.settings", user_id=user.get("id"), entity_type="scheduling_settings",
                                   entity_id=1, new_value=values, request=request)
    message = ("Saved. New and changed appointments will be written to technicians' calendars."
               if values["m365_company_id"] else "Saved. Calendar sync is off.")
    return flash_redirect(PAGE_PATH + "#schedule-settings", message, "success")


async def ticket_appointments_context(ticket_id: int) -> dict[str, Any]:
    """The ticket page's Appointments card."""

    settings = await scheduling.load_settings()
    items = []
    for appointment in await repo.list_for_ticket(ticket_id):
        items.append({**appointment, "when": scheduling.describe_when(appointment, settings.zone),
                      "kind_label": scheduling.KINDS.get(appointment["kind"], "Appointment"),
                      "technician": scheduling.technician_name(appointment),
                      "day": scheduling.to_local(appointment["starts_at"], settings.zone).date().isoformat()})
    upcoming = [item for item in items if item["status"] == "scheduled"]
    return {"items": items, "upcoming": len(upcoming), "timezone": settings.timezone,
            "sync_labels": scheduling.SYNC_LABELS, "statuses": scheduling.STATUSES}


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


class AppointmentIn(BaseModel):
    ticket_id: int
    technician_user_id: int
    kind: str = Field(default="onsite", description="onsite, remote, phone or meeting")
    date: DateType = Field(description="Local date in the scheduling time zone")
    start_time: str = Field(description="Local start time, HH:MM", pattern=r"^\d{2}:\d{2}$")
    duration_minutes: int = Field(ge=5, le=scheduling.MAX_DURATION_MINUTES)
    location: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=4000)
    status: str = Field(default="scheduled", description="scheduled, completed or cancelled")
    book_anyway: bool = Field(default=False, description="Save even if the technician is already booked then")


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
        overlapping = await scheduling.conflicts(values, exclude_id=appointment_id)
        if overlapping and not payload.book_anyway:
            raise scheduling.AppointmentError({"conflict": scheduling.conflict_message(overlapping, settings.zone)})
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


@router.get("/api/scheduling/settings", summary="Scheduling settings")
async def api_settings(_: dict = Depends(require_super_admin)) -> dict[str, Any]:
    settings = await scheduling.load_settings()
    return {"m365_company_id": settings.m365_company_id, "timezone": settings.timezone,
            "day_start_hour": settings.day_start_hour, "day_end_hour": settings.day_end_hour,
            "default_duration_minutes": settings.default_duration_minutes}


__all__ = ["router", "ticket_appointments_context"]
