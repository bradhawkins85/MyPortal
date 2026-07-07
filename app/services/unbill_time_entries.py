from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.repositories import ticket_billed_time_entries as billed_time_repo
from app.repositories import tickets as tickets_repo
from app.services import invoice_generator as invoice_generator_service

_UNBILLED_INVOICE_NUMBER = "UNBILLED_BY_AUTOMATION"


def _display_ticket_label(ticket: Mapping[str, Any]) -> str:
    ticket_number = str(ticket.get("ticket_number") or "").strip()
    subject = str(ticket.get("subject") or "").strip()
    if ticket_number and subject:
        return f"Ticket #{ticket_number}: {subject}"
    if subject:
        return subject
    return f"Ticket #{ticket.get('id')}"


async def preview_unbill_time_entries(company_id: int | None = None, *, limit: int = 1000) -> dict[str, Any]:
    """Preview billable, unbilled time entries that would be excluded from invoices."""
    tickets = await tickets_repo.list_tickets(company_id=company_id, limit=limit)
    items: list[dict[str, Any]] = []
    total_minutes = 0
    for ticket in tickets:
        ticket_id = ticket.get("id")
        if not ticket_id:
            continue
        unbilled_ids = await billed_time_repo.get_unbilled_reply_ids(int(ticket_id))
        if not unbilled_ids:
            continue
        replies = await tickets_repo.list_replies(int(ticket_id), include_internal=True)
        for reply in replies:
            reply_id = reply.get("id")
            if reply_id not in unbilled_ids or not reply.get("is_billable"):
                continue
            minutes = invoice_generator_service._coerce_minutes(reply.get("minutes_spent"))
            if minutes <= 0:
                continue
            total_minutes += minutes
            items.append({
                "type": "time_entry",
                "id": int(reply_id),
                "ticketId": int(ticket_id),
                "label": _display_ticket_label(ticket),
                "minutes": minutes,
                "hours": str(invoice_generator_service._minutes_to_hours(minutes)),
                "labourType": reply.get("labour_type_name") or reply.get("labour_type_code"),
                "action": "Mark this billable time entry as already billed so invoice generation skips it",
            })
    return {
        "status": "ready" if items else "skipped",
        "command": "unbill_time_entries",
        "company_id": company_id,
        "summary": (
            f"{len(items)} billable time entr{'y' if len(items) == 1 else 'ies'} will be marked as already billed."
            if items else "No billable, unbilled time entries were found."
        ),
        "totals": {"timeEntryCount": len(items), "minutes": total_minutes},
        "items": items,
    }


async def unbill_time_entries(company_id: int | None = None, *, limit: int = 1000) -> dict[str, Any]:
    """Mark billable, unbilled time entries as billed without creating invoices."""
    preview = await preview_unbill_time_entries(company_id, limit=limit)
    reply_ids = [int(item["id"]) for item in preview.get("items", []) if item.get("id")]
    if not reply_ids:
        return preview
    marked = await billed_time_repo.mark_replies_billed(
        reply_ids,
        xero_invoice_number=_UNBILLED_INVOICE_NUMBER,
        billed_at=datetime.now(timezone.utc),
    )
    return {
        **preview,
        "status": "succeeded" if marked else "skipped",
        "markedTimeEntries": marked,
        "summary": f"Marked {marked} billable time entr{'y' if marked == 1 else 'ies'} as already billed.",
    }
