from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from app.repositories import slas as sla_repo


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def calculate_status(row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    if not row.get("sla_id"):
        return {"state": "not_applicable", "label": "No SLA"}
    now = _utc(now) or datetime.now(timezone.utc)
    created = _utc(row.get("created_at")) or now
    first_response = _utc(row.get("first_response_at"))
    closed = _utc(row.get("closed_at"))
    response_due = created.timestamp() + int(row["response_minutes"]) * 60
    resolution_due = created.timestamp() + int(row["resolution_minutes"]) * 60
    response_breached = (first_response or now).timestamp() > response_due
    resolution_breached = (closed or now).timestamp() > resolution_due
    terminal = str(row.get("status") or "").lower() in {"closed", "resolved"}
    if response_breached or resolution_breached:
        state, label = "breached", "Breached"
    elif terminal:
        state, label = "met", "Met"
    else:
        active_due = response_due if not first_response else resolution_due
        active_target_minutes = int(row["response_minutes"] if not first_response else row["resolution_minutes"])
        remaining = active_due - now.timestamp()
        if remaining <= 0.2 * active_target_minutes * 60:
            state, label = "at_risk", "At risk"
        else:
            state, label = "on_track", "On track"
    return {
        "state": state, "label": label, "name": row.get("sla_name"),
        "response_breached": response_breached, "resolution_breached": resolution_breached,
        "response_due_at": datetime.fromtimestamp(response_due, timezone.utc),
        "resolution_due_at": datetime.fromtimestamp(resolution_due, timezone.utc),
    }


async def statuses_for_tickets(ticket_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    rows = await sla_repo.list_ticket_sla_source(ticket_ids)
    return {int(row["id"]): calculate_status(row) for row in rows}


async def emit_due_events() -> int:
    """Emit each SLA milestone once; called by the automation scheduler."""
    ticket_ids = await sla_repo.list_active_ticket_ids()
    if not ticket_ids:
        return 0
    from app.repositories import tickets as tickets_repo
    from app.services import automations

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
