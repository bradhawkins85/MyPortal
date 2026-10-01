from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from typing import Any, Sequence

from app.repositories import slas as sla_repo
from app.services import business_hours as business_hours_service
from app.services.business_hours import BusinessHoursSchedule


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def calculate_status(
    row: dict[str, Any],
    *,
    now: datetime | None = None,
    schedule: BusinessHoursSchedule | None = None,
) -> dict[str, Any]:
    """Return SLA state for a ticket.

    When ``schedule`` is supplied (templates with ``business_hours_only``),
    targets only elapse during that schedule's open hours.
    """

    if not row.get("sla_id"):
        return {"state": "not_applicable", "label": "No SLA"}
    now = _utc(now) or datetime.now(timezone.utc)
    created = _utc(row.get("created_at")) or now
    first_response = _utc(row.get("first_response_at"))
    closed = _utc(row.get("closed_at"))
    paused_seconds = max(0, float(row.get("paused_seconds") or 0))
    response_paused_seconds = max(0, float(row.get("response_paused_seconds") or 0))
    response_target = int(row["response_minutes"]) * 60 + response_paused_seconds
    resolution_target = int(row["resolution_minutes"]) * 60 + paused_seconds
    if schedule is not None:
        response_due_at = business_hours_service.add_business_seconds(schedule, created, response_target)
        resolution_due_at = business_hours_service.add_business_seconds(schedule, created, resolution_target)
    else:
        response_due_at = datetime.fromtimestamp(created.timestamp() + response_target, timezone.utc)
        resolution_due_at = datetime.fromtimestamp(created.timestamp() + resolution_target, timezone.utc)
    response_breached = (first_response or now) > response_due_at
    resolution_breached = (closed or now) > resolution_due_at
    terminal = str(row.get("status") or "").lower() in {"closed", "resolved"}
    if response_breached or resolution_breached:
        state, label = "breached", "Breached"
    elif terminal:
        state, label = "met", "Met"
    else:
        active_due = response_due_at if not first_response else resolution_due_at
        active_target_minutes = int(row["response_minutes"] if not first_response else row["resolution_minutes"])
        if schedule is not None:
            remaining = business_hours_service.business_seconds_between(schedule, now, active_due)
        else:
            remaining = (active_due - now).total_seconds()
        if remaining <= 0.2 * active_target_minutes * 60:
            state, label = "at_risk", "At risk"
        else:
            state, label = "on_track", "On track"
    status_paused = bool(row.get("sla_pause_status")) and not terminal
    outside_hours = (
        schedule is not None and not terminal and not business_hours_service.is_open(schedule, now)
    )
    return {
        "state": state, "label": label, "name": row.get("sla_name"),
        "paused": status_paused or outside_hours,
        "paused_reason": (
            "status" if status_paused else ("outside_business_hours" if outside_hours else None)
        ),
        "business_hours_only": schedule is not None,
        "response_breached": response_breached, "resolution_breached": resolution_breached,
        "response_due_at": response_due_at,
        "resolution_due_at": resolution_due_at,
    }


async def _schedules_for_rows(rows: Sequence[dict[str, Any]]) -> dict[int, BusinessHoursSchedule | None]:
    schedules: dict[int, BusinessHoursSchedule | None] = {}
    for row in rows:
        if not row.get("sla_id") or not row.get("business_hours_only"):
            continue
        company_id = row.get("company_id")
        company_key = int(company_id) if company_id is not None else None
        schedule = await business_hours_service.get_schedule(company_key)
        if schedule is not None and not schedule.has_open_time():
            schedule = None
        schedules[int(row["id"])] = schedule
    return schedules


async def statuses_for_tickets(ticket_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    rows = await sla_repo.list_ticket_sla_source(ticket_ids)
    schedules = await _schedules_for_rows(rows)
    paused_by_ticket: dict[int, float] = {}
    response_paused_by_ticket: dict[int, float] = {}
    first_response_by_ticket = {
        int(row["id"]): _utc(row.get("first_response_at")) for row in rows
    }
    now = datetime.now(timezone.utc)

    def _elapsed(ticket_id: int, start: datetime, end: datetime) -> float:
        schedule = schedules.get(ticket_id)
        if schedule is not None:
            return business_hours_service.business_seconds_between(schedule, start, end)
        return (end - start).total_seconds()

    for period in await sla_repo.list_pause_periods(ticket_ids):
        started = _utc(period.get("started_at"))
        ended = _utc(period.get("ended_at")) or now
        if started and ended and ended > started:
            ticket_id = int(period["ticket_id"])
            paused_by_ticket[ticket_id] = paused_by_ticket.get(ticket_id, 0) + _elapsed(ticket_id, started, ended)
            response_end = first_response_by_ticket.get(ticket_id) or now
            if started < response_end:
                response_paused_by_ticket[ticket_id] = response_paused_by_ticket.get(ticket_id, 0) + _elapsed(
                    ticket_id, started, min(ended, response_end)
                )
    for row in rows:
        row["paused_seconds"] = paused_by_ticket.get(int(row["id"]), 0)
        row["response_paused_seconds"] = response_paused_by_ticket.get(int(row["id"]), 0)
    return {
        int(row["id"]): calculate_status(row, now=now, schedule=schedules.get(int(row["id"])))
        for row in rows
    }


async def emit_due_events() -> int:
    """Emit each SLA milestone once; called by the automation scheduler."""
    ticket_ids = await sla_repo.list_active_ticket_ids()
    if not ticket_ids:
        return 0
    from app.repositories import tickets as tickets_repo
    automations = import_module("app.services.automations")

    statuses = await statuses_for_tickets(ticket_ids)
    emitted = 0
    for ticket_id, sla in statuses.items():
        events: list[str] = []
        if sla.get("state") == "at_risk":
            events.append("tickets.sla_at_risk")
        if sla.get("response_breached"):
            events.append("tickets.sla_response_breached")
        if sla.get("resolution_breached"):
            events.append("tickets.sla_resolution_breached")
        if not events:
            continue
        ticket = await tickets_repo.get_ticket(ticket_id)
        if not ticket:
            continue
        context = {"ticket": {**ticket, "sla": sla}, "sla": sla}
        for event_name in events:
            if await sla_repo.claim_event(ticket_id, event_name):
                await automations.handle_event(event_name, context)
                emitted += 1
    return emitted
