"""Technician scheduling and dispatch.

An appointment books a technician for a ticket at a time: an onsite visit,
remote session, phone call or meeting. Times are entered in the scheduling
time zone and stored in UTC.

* **Dispatch board** (:func:`board`): one row per technician across the
  working day, with tickets that have no upcoming appointment alongside.
* **Conflicts**: booking a technician over another of their appointments asks
  for confirmation first (:func:`conflicts`).
* **Tickets**: booking, moving, completing and cancelling an appointment adds
  an internal note to its ticket and fires the ``appointments.scheduled``,
  ``appointments.updated`` or ``appointments.cancelled`` automation event, so
  automations can, for example, email the requester.
* **Microsoft 365 calendars**: when a company's Microsoft 365 connection is
  chosen in the settings, each appointment is written to the technician's own
  calendar (``/users/{email}/events``, application permission
  ``Calendars.ReadWrite``), moved when it changes and removed when cancelled.
  Writes happen in the background; failures are recorded on the appointment
  and retried by :func:`sync_job` up to :data:`MAX_SYNC_ATTEMPTS` times.
* **Busy time**: the same job reads each technician's calendar
  (``/users/{email}/calendarView``) for the next :data:`BUSY_LOOKAHEAD_DAYS`
  days and stores only *when* they are busy, never what the event is: the
  request selects start, end and free/busy state only. The board shows these
  windows as "Unavailable", and booking a technician over them, or over
  another of their appointments, is refused (:func:`check_available`).
"""

from __future__ import annotations

import asyncio
import html
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

from app.repositories import appointments as repo

DEFAULT_KINDS = (
    {"kind_key": "onsite", "label": "Onsite visit", "tone": "warning", "is_default": True},
    {"kind_key": "remote", "label": "Remote session", "tone": "accent", "is_default": False},
    {"kind_key": "phone", "label": "Phone call", "tone": "success", "is_default": False},
    {"kind_key": "meeting", "label": "Meeting", "tone": "neutral", "is_default": False},
)
TONES = {"accent": "Blue", "warning": "Amber", "success": "Green", "danger": "Red", "neutral": "Grey"}
MAX_KIND_LABEL = 80
# Labels of every booking kind, archived ones included, so older appointments stay readable.
# Kept up to date in place by refresh_kinds() (called by load_settings()).
KINDS: dict[str, str] = {kind["kind_key"]: kind["label"] for kind in DEFAULT_KINDS}
KIND_TONES: dict[str, str] = {kind["kind_key"]: kind["tone"] for kind in DEFAULT_KINDS}
_kind_state: dict[str, Any] = {"active": [kind["kind_key"] for kind in DEFAULT_KINDS], "default": "onsite"}
STATUSES = {"scheduled": "Scheduled", "completed": "Completed", "cancelled": "Cancelled"}
SYNC_LABELS = {"off": "Not synced", "pending": "Syncing", "synced": "In calendar", "failed": "Calendar sync failed"}
DURATIONS = (15, 30, 45, 60, 90, 120, 180, 240, 480)
MAX_DURATION_MINUTES = 24 * 60
MAX_SYNC_ATTEMPTS = 5
SYNC_INTERVAL_SECONDS = 600
SYNC_STARTUP_DELAY_SECONDS = 150
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
BUSY_LOOKAHEAD_DAYS = 60
BUSY_LOOKBACK_DAYS = 1
# Graph ``showAs`` values that make a technician unavailable; "free" and
# "workingElsewhere" leave them bookable.
BUSY_SHOW_AS = ("oof", "busy", "tentative")
BUSY_LABELS = {"busy": "Busy", "tentative": "Tentative", "oof": "Away"}
EVENTS = {"created": "appointments.scheduled", "updated": "appointments.updated", "cancelled": "appointments.cancelled"}


class AppointmentError(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(errors.values()))
        self.errors = errors


@dataclass(frozen=True)
class Settings:
    m365_company_id: int | None = None
    timezone: str = "UTC"
    day_start_hour: int = 7
    day_end_hour: int = 19
    default_duration_minutes: int = 60

    @property
    def zone(self) -> ZoneInfo:
        return _zone(self.timezone)

    @property
    def calendar_sync(self) -> bool:
        return self.m365_company_id is not None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _zone(name: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _naive_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str) and value:
        try:
            return _naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def to_local(value: Any, zone: ZoneInfo) -> datetime | None:
    utc = _naive_utc(value)
    return utc.replace(tzinfo=timezone.utc).astimezone(zone) if utc else None


def to_utc(local: datetime, zone: ZoneInfo) -> datetime:
    aware = local if local.tzinfo else local.replace(tzinfo=zone)
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def technician_name(user: Mapping[str, Any]) -> str:
    first = user.get("first_name") or user.get("technician_first_name")
    last = user.get("last_name") or user.get("technician_last_name")
    name = " ".join(part for part in (first, last) if part).strip()
    return name or str(user.get("email") or user.get("technician_email") or "Technician")


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


async def load_settings() -> Settings:
    from app.services import business_hours as business_hours_service

    await refresh_kinds()
    row = await repo.get_settings()
    start = _int(row.get("day_start_hour"), 7, 0, 23)
    end = _int(row.get("day_end_hour"), 19, 1, 24)
    if end <= start:
        start, end = 7, 19
    return Settings(
        m365_company_id=int(row["m365_company_id"]) if row.get("m365_company_id") else None,
        timezone=str(row.get("timezone") or business_hours_service.default_timezone_name()),
        day_start_hour=start,
        day_end_hour=end,
        default_duration_minutes=_int(row.get("default_duration_minutes"), 60, 5, MAX_DURATION_MINUTES),
    )


# --------------------------------------------------------------------------- #
# Booking kinds
# --------------------------------------------------------------------------- #


async def refresh_kinds() -> list[dict[str, Any]]:
    """Load the booking kinds, falling back to the built-in ones if none are stored."""

    try:
        rows = await repo.list_kinds()
    except Exception as exc:  # noqa: BLE001 - before migration 585, or no database
        logger.debug("Booking kinds unavailable, using the built-in kinds: {}", exc)
        rows = []
    if not rows:
        rows = [{**kind, "id": None, "is_archived": False, "appointment_count": 0} for kind in DEFAULT_KINDS]
    KINDS.clear()
    KINDS.update({row["kind_key"]: row["label"] for row in rows})
    KIND_TONES.clear()
    KIND_TONES.update({row["kind_key"]: row["tone"] if row.get("tone") in TONES else "accent" for row in rows})
    active = [row["kind_key"] for row in rows if not row.get("is_archived")] or [rows[0]["kind_key"]]
    default = next((row["kind_key"] for row in rows if row.get("is_default") and not row.get("is_archived")), active[0])
    _kind_state.update(active=active, default=default)
    return rows


def active_kinds() -> dict[str, str]:
    """Kinds offered for new bookings, in display order."""

    return {key: KINDS[key] for key in _kind_state["active"] if key in KINDS}


def default_kind() -> str:
    return str(_kind_state["default"])


def kind_label(key: Any) -> str:
    return KINDS.get(str(key), "Appointment")


def kind_key_for(label: str, taken: Iterable[str]) -> str:
    """A short unique key for a new kind, derived from its label."""

    base = "".join(char if char.isalnum() else "-" for char in label.casefold()).strip("-")
    base = "-".join(part for part in base.split("-") if part)[:12].strip("-") or "kind"
    used = set(taken)
    key, number = base, 2
    while key in used:
        key = f"{base}-{number}"
        number += 1
    return key


def parse_kind_form(form: Mapping[str, Any]) -> dict[str, Any]:
    label = " ".join(str(form.get("label") or "").split())
    errors: dict[str, str] = {}
    if not label:
        errors["label"] = "Give the booking kind a name."
    elif len(label) > MAX_KIND_LABEL:
        errors["label"] = f"Keep the name to {MAX_KIND_LABEL} characters."
    tone = str(form.get("tone") or "accent")
    if tone not in TONES:
        errors["tone"] = "Choose a colour."
    if errors:
        raise AppointmentError(errors)
    return {"label": label, "tone": tone, "is_default": bool(form.get("is_default"))}


def parse_settings_form(form: Mapping[str, Any]) -> dict[str, Any]:
    errors: dict[str, str] = {}
    tz_name = str(form.get("timezone") or "UTC").strip()
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        errors["timezone"] = "Choose a time zone from the list."
    start = _int(form.get("day_start_hour"), -1, -1, 24)
    end = _int(form.get("day_end_hour"), -1, -1, 24)
    if not (0 <= start < end <= 24):
        errors["day_end_hour"] = "The working day must end after it starts."
    duration = _int(form.get("default_duration_minutes"), -1, -1, MAX_DURATION_MINUTES)
    if duration < 5:
        errors["default_duration_minutes"] = "Choose a default length."
    company = str(form.get("m365_company_id") or "").strip()
    if errors:
        raise AppointmentError(errors)
    return {"m365_company_id": int(company) if company.isdigit() else None, "timezone": tz_name,
            "day_start_hour": start, "day_end_hour": end, "default_duration_minutes": duration}


# --------------------------------------------------------------------------- #
# Appointments
# --------------------------------------------------------------------------- #


def parse_form(form: Mapping[str, Any], settings: Settings, *, technician_ids: Iterable[int]) -> dict[str, Any]:
    """Validate the editor. Times are local to the scheduling time zone."""

    errors: dict[str, str] = {}
    try:
        ticket_id = int(str(form.get("ticket_id") or "").strip().lstrip("#"))
    except ValueError:
        ticket_id = 0
    if ticket_id <= 0:
        errors["ticket_id"] = "Choose the ticket the appointment is for."
    try:
        technician_id = int(str(form.get("technician_user_id") or ""))
    except ValueError:
        technician_id = 0
    if technician_id not in set(technician_ids):
        errors["technician_user_id"] = "Choose a technician."
    kind = str(form.get("kind") or default_kind())
    if kind not in KINDS:
        errors["kind"] = "Choose the kind of appointment."
    try:
        day = date.fromisoformat(str(form.get("date") or "").strip())
    except ValueError:
        day = None
        errors["date"] = "Choose the date."
    start_text = str(form.get("start_time") or "").strip()
    try:
        start_time = time.fromisoformat(start_text[:5]) if start_text else None
    except ValueError:
        start_time = None
    if start_time is None:
        errors["start_time"] = "Enter the start time, such as 09:30."
    duration = _int(form.get("duration_minutes"), -1, -1, MAX_DURATION_MINUTES)
    if duration < 5:
        errors["duration_minutes"] = "Choose how long it takes."
    status = str(form.get("status") or "scheduled")
    if status not in STATUSES:
        status = "scheduled"
    if errors:
        raise AppointmentError(errors)
    local_start = datetime.combine(day, start_time)
    starts_at = to_utc(local_start, settings.zone)
    return {
        "ticket_id": ticket_id,
        "technician_user_id": technician_id,
        "kind": kind,
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(minutes=duration),
        "location": " ".join(str(form.get("location") or "").split())[:500] or None,
        "notes": str(form.get("notes") or "").strip()[:4000] or None,
        "status": status,
    }


def move_values(appointment: Mapping[str, Any], settings: Settings, *, technician_ids: Iterable[int],
                technician_user_id: int, day: date, start_time: str) -> dict[str, Any]:
    """Validated values for dragging ``appointment`` to another technician and/or start time.

    Everything else (ticket, kind, length, location, notes) stays as it was. Only
    scheduled appointments can be moved.
    """

    if appointment.get("status") != "scheduled":
        raise AppointmentError({"status": "Only scheduled appointments can be moved."})
    state = editor_state(appointment, settings)
    state.update(technician_user_id=technician_user_id, date=day.isoformat(), start_time=start_time)
    return parse_form({key: "" if value is None else str(value) for key, value in state.items()}, settings,
                      technician_ids=technician_ids)


async def conflicts(values: Mapping[str, Any], *, exclude_id: int | None = None) -> list[dict[str, Any]]:
    if values.get("status") != "scheduled":
        return []
    return await repo.list_overlapping(int(values["technician_user_id"]), values["starts_at"], values["ends_at"],
                                       exclude_id)


async def busy_conflicts(values: Mapping[str, Any], settings: Settings) -> list[dict[str, Any]]:
    """Time the technician's own calendar shows them unavailable during the booking."""

    if values.get("status") != "scheduled" or not settings.calendar_sync:
        return []
    return await repo.list_busy_between(values["starts_at"], values["ends_at"],
                                        technician_user_id=int(values["technician_user_id"]))


def _unchanged_time(values: Mapping[str, Any], previous: Mapping[str, Any] | None) -> bool:
    if not previous or previous.get("status") != "scheduled":
        return False
    return (int(previous["technician_user_id"]) == int(values["technician_user_id"])
            and _naive_utc(previous["starts_at"]) == _naive_utc(values["starts_at"])
            and _naive_utc(previous["ends_at"]) == _naive_utc(values["ends_at"]))


async def check_available(values: Mapping[str, Any], settings: Settings, *, appointment_id: int | None = None,
                          previous: Mapping[str, Any] | None = None, technician: str | None = None) -> None:
    """Refuse a booking over another appointment or over time the technician's calendar shows busy.

    Saving an appointment without moving it (changing its notes, say) is not
    re-checked, so time that became busy afterwards doesn't lock it.
    """

    if values.get("status") != "scheduled" or _unchanged_time(values, previous):
        return
    booked = await conflicts(values, exclude_id=appointment_id)
    if booked:
        raise AppointmentError({"conflict": conflict_message(booked, settings.zone)})
    busy = await busy_conflicts(values, settings)
    if busy:
        raise AppointmentError({"conflict": unavailable_message(busy, settings.zone, technician=technician)})


def describe_when(appointment: Mapping[str, Any], zone: ZoneInfo) -> str:
    start, end = to_local(appointment["starts_at"], zone), to_local(appointment["ends_at"], zone)
    if not start or not end:
        return ""
    if start.date() == end.date():
        return f"{start:%a %d %b %Y, %H:%M}–{end:%H:%M}"
    return f"{start:%a %d %b %Y %H:%M} – {end:%a %d %b %Y %H:%M}"


def conflict_message(found: Sequence[Mapping[str, Any]], zone: ZoneInfo) -> str:
    items = "; ".join(f"#{item.get('ticket_number') or item['ticket_id']} {describe_when(item, zone)}" for item in found[:3])
    return f"{technician_name(found[0])} is already booked then ({items}). Choose another time or technician."


def unavailable_message(found: Sequence[Mapping[str, Any]], zone: ZoneInfo, *, technician: str | None = None) -> str:
    times = "; ".join(describe_when(item, zone) for item in found[:3])
    who = technician or "The technician"
    return f"{who} is unavailable then: their calendar shows them busy ({times}). Choose another time or technician."


def _note(action: str, appointment: Mapping[str, Any], technician: str, zone: ZoneInfo) -> str:
    kind = kind_label(appointment.get("kind"))
    when = html.escape(describe_when(appointment, zone))
    parts = [f"<p><strong>{html.escape(kind)} {action}</strong> with {html.escape(technician)}: {when} ({zone.key}).</p>"]
    if appointment.get("location"):
        parts.append(f"<p><strong>Where:</strong> {html.escape(str(appointment['location']))}</p>")
    if appointment.get("notes") and action != "cancelled":
        parts.append(f"<p>{html.escape(str(appointment['notes']))}</p>")
    return "".join(parts)


async def _after_change(event: str, note_action: str, appointment: Mapping[str, Any], *,
                        user: Mapping[str, Any] | None, settings: Settings,
                        remove_from: Mapping[str, Any] | None = None) -> None:
    """Note the change on the ticket, fire the automation event and sync the calendar."""

    from app.repositories import tickets as tickets_repo
    from app.services import automations as automations_service
    from app.services import tickets as tickets_service

    technician = technician_name(appointment)
    ticket_id = int(appointment["ticket_id"])
    try:
        await tickets_repo.create_reply(
            ticket_id=ticket_id,
            author_id=int(user["id"]) if user and user.get("id") else None,
            body=_note(note_action, appointment, technician, settings.zone),
            is_internal=True,
        )
        await tickets_service.broadcast_ticket_event(action="reply", ticket_id=ticket_id)
    except Exception as exc:  # noqa: BLE001 - the appointment is already saved
        logger.warning("Could not note appointment {} on ticket {}: {}", appointment.get("id"), ticket_id, exc)
    try:
        ticket = await tickets_repo.get_ticket(ticket_id)
        if ticket:
            enriched = await tickets_service._enrich_ticket_context(ticket)  # same context as ticket events
            await automations_service.handle_event(EVENTS[event], {
                "ticket": enriched,
                "appointment": appointment_context(appointment, settings),
            })
    except Exception as exc:  # noqa: BLE001 - automations must not undo the booking
        logger.warning("Appointment automation failed for {}: {}", appointment.get("id"), exc)
    if settings.calendar_sync:
        schedule_sync(int(appointment["id"]), remove_from=remove_from)


def appointment_context(appointment: Mapping[str, Any], settings: Settings) -> dict[str, Any]:
    start, end = to_local(appointment["starts_at"], settings.zone), to_local(appointment["ends_at"], settings.zone)
    return {
        "id": appointment.get("id"),
        "kind": appointment.get("kind"),
        "kind_label": kind_label(appointment.get("kind")),
        "status": appointment.get("status"),
        "technician": technician_name(appointment),
        "technician_email": appointment.get("technician_email"),
        "when": describe_when(appointment, settings.zone),
        "date": f"{start:%A %d %B %Y}" if start else "",
        "start_time": f"{start:%H:%M}" if start else "",
        "end_time": f"{end:%H:%M}" if end else "",
        "timezone": settings.timezone,
        "starts_at_utc": _naive_utc(appointment["starts_at"]).isoformat() + "Z",
        "location": appointment.get("location") or "",
        "notes": appointment.get("notes") or "",
    }


async def save(values: dict[str, Any], *, appointment_id: int | None, user: Mapping[str, Any] | None,
               settings: Settings) -> dict[str, Any]:
    from app.repositories import tickets as tickets_repo

    ticket = await tickets_repo.get_ticket(int(values["ticket_id"]))
    if not ticket:
        raise AppointmentError({"ticket_id": "That ticket doesn't exist."})
    values = {**values, "company_id": ticket.get("company_id")}
    sync_status = "pending" if settings.calendar_sync else "off"
    if appointment_id is None:
        appointment_id = await repo.create(values, user_id=(user or {}).get("id"), sync_status=sync_status)
        event, note_action = "created", "booked"
        remove_from = None
    else:
        previous = await repo.get(appointment_id)
        await repo.update(appointment_id, values, sync_status=sync_status)
        moved_technician = previous and int(previous["technician_user_id"]) != int(values["technician_user_id"])
        # The event lives in the previous technician's calendar: the sync removes it there first.
        remove_from = previous if moved_technician and previous.get("m365_event_id") else None
        note_action = {"cancelled": "cancelled", "completed": "completed"}.get(values["status"], "updated")
        event = "cancelled" if values["status"] == "cancelled" else "updated"
    saved = await repo.get(appointment_id) or {}
    await _after_change(event, note_action, saved, user=user, settings=settings, remove_from=remove_from)
    return saved


async def set_status(appointment_id: int, status: str, *, user: Mapping[str, Any] | None,
                     settings: Settings) -> dict[str, Any] | None:
    if status not in STATUSES:
        raise AppointmentError({"status": "Unknown status."})
    await repo.set_status(appointment_id, status, sync_status="pending" if settings.calendar_sync else "off")
    saved = await repo.get(appointment_id)
    if saved:
        event = "cancelled" if status == "cancelled" else "updated"
        await _after_change(event, status if status != "scheduled" else "updated", saved, user=user, settings=settings)
    return saved


# --------------------------------------------------------------------------- #
# Microsoft 365 calendar
# --------------------------------------------------------------------------- #


_sync_tasks: set[asyncio.Task[Any]] = set()


def schedule_sync(appointment_id: int, *, remove_from: Mapping[str, Any] | None = None) -> None:
    try:
        task = asyncio.get_running_loop().create_task(sync_appointment(appointment_id, remove_from=remove_from))
    except RuntimeError:  # pragma: no cover - no loop (scripts); the retry job picks it up
        return
    _sync_tasks.add(task)
    task.add_done_callback(_sync_tasks.discard)


def event_payload(appointment: Mapping[str, Any], *, portal_url: str) -> dict[str, Any]:
    kind = kind_label(appointment.get("kind"))
    number = appointment.get("ticket_number") or appointment.get("ticket_id")
    subject = f"{kind}: {appointment.get('ticket_subject') or 'Ticket'} (#{number})"
    lines = [f"<p><strong>{html.escape(kind)}</strong> for ticket #{html.escape(str(number))}"
             + (f" at {html.escape(str(appointment['company_name']))}" if appointment.get("company_name") else "")
             + ".</p>"]
    if appointment.get("notes"):
        lines.append(f"<p>{html.escape(str(appointment['notes']))}</p>")
    if portal_url:
        link = f"{portal_url.rstrip('/')}/admin/tickets/{int(appointment['ticket_id'])}"
        lines.append(f'<p><a href="{html.escape(link)}">Open the ticket in MyPortal</a></p>')
    payload: dict[str, Any] = {
        "subject": subject[:255],
        "body": {"contentType": "HTML", "content": "".join(lines)},
        "start": {"dateTime": _naive_utc(appointment["starts_at"]).isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": _naive_utc(appointment["ends_at"]).isoformat(), "timeZone": "UTC"},
        "showAs": "busy",
        "isReminderOn": True,
        "reminderMinutesBeforeStart": 15,
        "categories": ["MyPortal"],
    }
    if appointment.get("location"):
        payload["location"] = {"displayName": str(appointment["location"])[:255]}
    return payload


def _events_url(mailbox: str, event_id: str | None = None) -> str:
    url = f"{GRAPH_BASE}/users/{quote(mailbox, safe='@.')}/events"
    return f"{url}/{quote(event_id, safe='')}" if event_id else url


async def sync_appointment(appointment_id: int, *, remove_from: Mapping[str, Any] | None = None) -> str:
    """Write one appointment to its technician's calendar. Returns the sync status."""

    from app.core.config import get_settings as get_app_settings
    from app.services import m365 as m365_service

    settings = await load_settings()
    appointment = await repo.get(appointment_id)
    if not appointment:
        return "missing"
    if not settings.calendar_sync:
        await repo.record_sync(appointment_id, sync_status="off", event_id=appointment.get("m365_event_id"),
                               mailbox=appointment.get("m365_mailbox"), error=None)
        return "off"
    mailbox = str(appointment.get("technician_email") or "").strip()
    event_id = appointment.get("m365_event_id")
    try:
        token = await m365_service.acquire_access_token(int(settings.m365_company_id), force_client_credentials=True)
        if remove_from is not None and remove_from.get("m365_event_id") and remove_from.get("m365_mailbox"):
            await _delete_event(m365_service, token, str(remove_from["m365_mailbox"]), str(remove_from["m365_event_id"]))
            event_id = None
        elif event_id and appointment.get("m365_mailbox") and appointment["m365_mailbox"] != mailbox:
            await _delete_event(m365_service, token, str(appointment["m365_mailbox"]), str(event_id))
            event_id = None
        if appointment["status"] == "cancelled":
            if event_id:
                await _delete_event(m365_service, token, mailbox, str(event_id))
            await repo.record_sync(appointment_id, sync_status="synced", event_id=None, mailbox=None, error=None)
            return "synced"
        if not mailbox:
            raise ValueError("The technician has no email address to find their calendar.")
        payload = event_payload(appointment, portal_url=str(get_app_settings().portal_url or ""))
        if event_id:
            await m365_service._graph_patch(token, _events_url(mailbox, str(event_id)), payload)
        else:
            payload["transactionId"] = f"myportal-appointment-{appointment_id}"
            created = await m365_service._graph_post(token, _events_url(mailbox), payload)
            event_id = str(created.get("id") or "") or None
        await repo.record_sync(appointment_id, sync_status="synced", event_id=event_id, mailbox=mailbox, error=None)
        return "synced"
    except Exception as exc:  # noqa: BLE001 - recorded and retried by the sync job
        message = str(exc) or type(exc).__name__
        if "403" in message or "Forbidden" in message or "AccessDenied" in message:
            message += " — grant the Microsoft 365 app the Calendars.ReadWrite application permission."
        logger.warning("Calendar sync failed for appointment {}: {}", appointment_id, message)
        await repo.record_sync(appointment_id, sync_status="failed", event_id=event_id, mailbox=mailbox or None,
                               error=message)
        return "failed"


async def _delete_event(m365_service: Any, token: str, mailbox: str, event_id: str) -> None:
    try:
        await m365_service._graph_delete(token, _events_url(mailbox, event_id))
    except m365_service.M365Error as exc:
        if getattr(exc, "http_status", None) == 404:
            return  # already gone from the calendar
        raise


async def sync_pending() -> int:
    if not (await load_settings()).calendar_sync:
        return 0
    count = 0
    for appointment in await repo.list_sync_due():
        await sync_appointment(int(appointment["id"]))
        count += 1
    return count


async def sync_job() -> None:
    from app.services.singleton_jobs import singleton_run

    @singleton_run("appointment_calendar_sync", ttl_seconds=SYNC_INTERVAL_SECONDS)
    async def _run_once() -> None:
        await sync_pending()
        await sync_busy_times()

    await asyncio.sleep(SYNC_STARTUP_DELAY_SECONDS)
    while True:
        try:
            await _run_once()
        except Exception as exc:  # pragma: no cover - retried on the next pass
            logger.warning("Appointment calendar sync job failed: {}", exc)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


# --------------------------------------------------------------------------- #
# Busy time from technicians' calendars
# --------------------------------------------------------------------------- #


def calendar_view_url(mailbox: str, start: datetime, end: datetime) -> str:
    """Only the fields needed to know *when* someone is busy: never subject, body or attendees."""

    query = urlencode({
        "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "$select": "id,start,end,showAs,isCancelled,transactionId",
        "$top": "250",
    })
    return f"{GRAPH_BASE}/users/{quote(mailbox, safe='@.')}/calendarView?{query}"


def _graph_time(value: Any) -> datetime | None:
    if not isinstance(value, Mapping) or not value.get("dateTime"):
        return None
    text = str(value["dateTime"])[:19]  # Graph sends up to seven fractional digits
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    zone_name = str(value.get("timeZone") or "UTC")
    if zone_name.upper() not in {"UTC", "ETC/UTC", "Z"}:
        try:
            return to_utc(parsed, ZoneInfo(zone_name))
        except (ZoneInfoNotFoundError, ValueError):
            pass  # the request asks for UTC; a Windows zone name here is unexpected
    return parsed


def busy_blocks(events: Iterable[Mapping[str, Any]], *, own_event_ids: Iterable[str] = ()) -> list[tuple[datetime, datetime, str]]:
    """Merge a calendar's busy events into ``(start, end, show_as)`` windows.

    Free and cancelled events, and the events MyPortal wrote for its own
    appointments, are left out. Overlapping and touching events merge, so the
    stored windows don't reveal how many events there were.
    """

    own = {str(event_id) for event_id in own_event_ids if event_id}
    rank = {name: index for index, name in enumerate(reversed(BUSY_SHOW_AS))}
    windows: list[tuple[datetime, datetime, str]] = []
    for event in events:
        show_as = str(event.get("showAs") or "busy")
        if show_as not in BUSY_SHOW_AS or event.get("isCancelled"):
            continue
        if str(event.get("id") or "") in own or str(event.get("transactionId") or "").startswith("myportal-appointment-"):
            continue
        start, end = _graph_time(event.get("start")), _graph_time(event.get("end"))
        if start is None or end is None or end <= start:
            continue
        windows.append((start, end, show_as))
    merged: list[tuple[datetime, datetime, str]] = []
    for start, end, show_as in sorted(windows):
        if merged and start <= merged[-1][1]:
            last_start, last_end, last_show = merged[-1]
            stronger = show_as if rank[show_as] > rank[last_show] else last_show
            merged[-1] = (last_start, max(last_end, end), stronger)
        else:
            merged.append((start, end, show_as))
    return merged


async def _calendar_events(m365_service: Any, token: str, url: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    headers = {"Prefer": 'outlook.timezone="UTC"'}
    pages = 0
    while url and pages < 40:
        data = await m365_service._graph_get(token, url, extra_headers=headers)
        items.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        pages += 1
    return items


async def sync_busy_times(*, now: datetime | None = None) -> int:
    """Import every technician's busy time. Returns how many technicians were updated."""

    from app.repositories import chat_auto_assign as technicians_repo
    from app.services import m365 as m365_service

    settings = await load_settings()
    if not settings.calendar_sync:
        return 0
    now = now or _utcnow()
    start = now - timedelta(days=BUSY_LOOKBACK_DAYS)
    end = now + timedelta(days=BUSY_LOOKAHEAD_DAYS)
    errors: list[str] = []
    updated = 0
    try:
        token = await m365_service.acquire_access_token(int(settings.m365_company_id), force_client_credentials=True)
    except Exception as exc:  # noqa: BLE001 - recorded for the settings card
        message = str(exc) or type(exc).__name__
        logger.warning("Could not read technicians' calendars: {}", message)
        await repo.record_busy_sync(synced_at=now, error=message)
        return 0
    for technician in await technicians_repo.list_all_technicians():
        mailbox = str(technician.get("email") or "").strip()
        if not mailbox:
            continue
        technician_id = int(technician["id"])
        try:
            events = await _calendar_events(m365_service, token, calendar_view_url(mailbox, start, end))
            own = [item.get("m365_event_id") for item in await repo.list_between(
                start, end, technician_user_id=technician_id, include_cancelled=True)]
            await repo.replace_busy_times(technician_id, start, end, busy_blocks(events, own_event_ids=own),
                                          synced_at=now)
            updated += 1
        except Exception as exc:  # noqa: BLE001 - one mailbox must not stop the others
            message = str(exc) or type(exc).__name__
            if "403" in message or "Forbidden" in message or "AccessDenied" in message:
                message += " — grant the Microsoft 365 app the Calendars.ReadWrite application permission."
            logger.warning("Could not read the calendar of technician {}: {}", technician_id, message)
            errors.append(f"{technician_name(technician)}: {message}")
    await repo.record_busy_sync(synced_at=now, error="; ".join(errors) or None)
    return updated


# --------------------------------------------------------------------------- #
# Board and lists
# --------------------------------------------------------------------------- #


def _block(appointment: Mapping[str, Any], day_start: datetime, day_end: datetime, zone: ZoneInfo) -> dict[str, Any]:
    start = to_local(appointment["starts_at"], zone)
    end = to_local(appointment["ends_at"], zone)
    span = (day_end - day_start).total_seconds()
    left = max(0.0, (start - day_start).total_seconds() / span)
    right = min(1.0, (end - day_start).total_seconds() / span)
    return {
        **dict(appointment),
        "left": round(100 * left, 2),
        "width": round(max(1.5, 100 * (right - left)), 2),
        "start_label": f"{start:%H:%M}",
        "end_label": f"{end:%H:%M}",
        "duration_minutes": int((end - start).total_seconds() // 60),
        "kind_label": kind_label(appointment.get("kind")),
        "kind_tone": KIND_TONES.get(str(appointment.get("kind")), "accent"),
        "starts_before": start < day_start,
        "ends_after": end > day_end,
        "when": describe_when(appointment, zone),
    }


def _busy_band(block: Mapping[str, Any], day_start: datetime, day_end: datetime, zone: ZoneInfo) -> dict[str, Any]:
    start = to_local(block["starts_at"], zone)
    end = to_local(block["ends_at"], zone)
    span = (day_end - day_start).total_seconds()
    left = max(0.0, (start - day_start).total_seconds() / span)
    right = min(1.0, (end - day_start).total_seconds() / span)
    return {
        "left": round(100 * left, 2),
        "width": round(max(0.5, 100 * (right - left)), 2),
        "start_label": f"{max(start, day_start):%H:%M}",
        "end_label": f"{min(end, day_end):%H:%M}",
        "label": BUSY_LABELS.get(str(block.get("show_as")), "Busy"),
        "when": describe_when(block, zone),
    }


def board(day: date, technicians: Sequence[Mapping[str, Any]], appointments: Sequence[Mapping[str, Any]],
          settings: Settings, busy: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """One row per technician across the working day, with appointments and unavailable time on it."""

    zone = settings.zone
    day_start = datetime.combine(day, time(settings.day_start_hour), tzinfo=zone)
    day_end = (datetime.combine(day, time(0), tzinfo=zone) + timedelta(hours=settings.day_end_hour))
    rows = []
    by_tech: dict[int, list[dict[str, Any]]] = {}
    for appointment in appointments:
        start, end = to_local(appointment["starts_at"], zone), to_local(appointment["ends_at"], zone)
        if end <= day_start or start >= day_end:
            continue
        by_tech.setdefault(int(appointment["technician_user_id"]), []).append(_block(appointment, day_start, day_end, zone))
    busy_by_tech: dict[int, list[dict[str, Any]]] = {}
    for block in busy:
        start, end = to_local(block["starts_at"], zone), to_local(block["ends_at"], zone)
        if end <= day_start or start >= day_end:
            continue
        busy_by_tech.setdefault(int(block["technician_user_id"]), []).append(_busy_band(block, day_start, day_end, zone))
    for technician in technicians:
        items = sorted(by_tech.get(int(technician["id"]), []), key=lambda item: item["left"])
        # Overlapping bookings stack into lanes so both stay visible.
        lanes: list[float] = []
        for item in items:
            lane = next((index for index, edge in enumerate(lanes) if edge <= item["left"]), None)
            if lane is None:
                lanes.append(0)
                lane = len(lanes) - 1
            lanes[lane] = item["left"] + item["width"]
            item["lane"] = lane
        booked = sum(min(item["width"], 100) for item in items if item.get("status") == "scheduled")
        unavailable = sorted(busy_by_tech.get(int(technician["id"]), []), key=lambda band: band["left"])
        rows.append({"technician": technician, "name": technician_name(technician), "items": items,
                     "busy": unavailable, "lanes": max(1, len(lanes)), "booked_percent": min(100, round(booked))})
    hours = [{"label": f"{hour:02d}:00", "left": round(100 * (hour - settings.day_start_hour)
                                                      / (settings.day_end_hour - settings.day_start_hour), 2)}
             for hour in range(settings.day_start_hour, settings.day_end_hour)]
    now = datetime.now(timezone.utc).astimezone(zone)
    now_left = None
    if day_start <= now <= day_end:
        now_left = round(100 * (now - day_start).total_seconds() / (day_end - day_start).total_seconds(), 2)
    return {"rows": rows, "hours": hours, "now_left": now_left, "day": day,
            "utc_start": to_utc(day_start, zone), "utc_end": to_utc(day_end, zone)}


def day_window(day: date, settings: Settings) -> tuple[datetime, datetime]:
    zone = settings.zone
    return (to_utc(datetime.combine(day, time(0), tzinfo=zone), zone),
            to_utc(datetime.combine(day + timedelta(days=1), time(0), tzinfo=zone), zone))


def group_by_day(appointments: Sequence[Mapping[str, Any]], zone: ZoneInfo) -> list[dict[str, Any]]:
    days: dict[date, list[dict[str, Any]]] = {}
    for appointment in appointments:
        start = to_local(appointment["starts_at"], zone)
        days.setdefault(start.date(), []).append({
            **dict(appointment), "when": describe_when(appointment, zone),
            "kind_label": kind_label(appointment.get("kind")),
            "kind_tone": KIND_TONES.get(str(appointment.get("kind")), "accent"),
        })
    return [{"day": day, "items": items} for day, items in sorted(days.items())]


def day_unavailable(day: date, appointments: Sequence[Mapping[str, Any]], busy: Sequence[Mapping[str, Any]],
                    settings: Settings, *, exclude_id: int | None = None) -> list[dict[str, str]]:
    """A technician's taken time on ``day`` as local ``HH:MM`` ranges, for the booking editor.

    Only times and whether it's an appointment or calendar time are given; no
    details of the technician's own calendar events.
    """

    zone = settings.zone
    day_start = datetime.combine(day, time(0), tzinfo=zone)
    day_end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=zone)
    ranges: list[dict[str, str]] = []
    items = [(item, "appointment") for item in appointments
             if item.get("status") == "scheduled" and item.get("id") != exclude_id]
    items += [(item, "calendar") for item in busy]
    for item, source in items:
        start, end = to_local(item["starts_at"], zone), to_local(item["ends_at"], zone)
        if not start or not end or end <= day_start or start >= day_end:
            continue
        start, end = max(start, day_start), min(end, day_end)
        ranges.append({"start": f"{start:%H:%M}", "end": "24:00" if end == day_end else f"{end:%H:%M}",
                       "source": source})
    return sorted(ranges, key=lambda item: (item["start"], item["end"]))


def editor_state(appointment: Mapping[str, Any] | None, settings: Settings, *, ticket_id: int | None = None,
                 technician_id: int | None = None, day: date | None = None, start: str | None = None) -> dict[str, Any]:
    if appointment:
        local_start = to_local(appointment["starts_at"], settings.zone)
        minutes = int((_naive_utc(appointment["ends_at"]) - _naive_utc(appointment["starts_at"])).total_seconds() // 60)
        return {
            "id": appointment["id"], "ticket_id": appointment["ticket_id"],
            "ticket_label": f"#{appointment.get('ticket_number') or appointment['ticket_id']} {appointment.get('ticket_subject') or ''}",
            "technician_user_id": appointment["technician_user_id"], "kind": appointment["kind"],
            "date": local_start.date().isoformat(), "start_time": f"{local_start:%H:%M}", "duration_minutes": minutes,
            "location": appointment.get("location") or "", "notes": appointment.get("notes") or "",
            "status": appointment["status"],
        }
    return {"id": None, "ticket_id": ticket_id, "technician_user_id": technician_id, "kind": default_kind(),
            "date": (day or datetime.now(settings.zone).date()).isoformat(), "start_time": start or "09:00",
            "duration_minutes": settings.default_duration_minutes, "location": "", "notes": "", "status": "scheduled"}
