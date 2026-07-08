from __future__ import annotations

from typing import Any, Mapping

from app.repositories import ticket_billed_time_entries as billed_time_repo
from app.repositories import tickets as tickets_repo
from app.services import invoice_generator as invoice_generator_service


def _display_ticket_label(ticket: Mapping[str, Any]) -> str:
    ticket_number = str(ticket.get("ticket_number") or "").strip()
    subject = str(ticket.get("subject") or "").strip()
    if ticket_number and subject:
        return f"Ticket #{ticket_number}: {subject}"
    if subject:
        return subject
    return f"Ticket #{ticket.get('id')}"


async def preview_unbill_time_entries(company_id: int | None = None, *, limit: int = 1000) -> dict[str, Any]:
    """Preview billable time entries that will be made non-billable.

    Time entries are read directly from ``ticket_replies`` in pages so the
    automation matches billable-time reports and does not miss entries on
    already-billed, closed, merged, or otherwise filtered tickets.
    """
    page_size = max(1, int(limit or 1000))
    offset = 0
    items: list[dict[str, Any]] = []
    total_minutes = 0
    while True:
        replies = await tickets_repo.list_billable_time_entries(
            company_id=company_id,
            limit=page_size,
            offset=offset,
        )
        if not replies:
            break
        for reply in replies:
            reply_id = reply.get("id")
            ticket_id = reply.get("ticket_id")
            if not reply_id or not ticket_id:
                continue
            minutes = invoice_generator_service._coerce_minutes(reply.get("minutes_spent"))
            if minutes <= 0:
                continue
            total_minutes += minutes
            items.append({
                "type": "time_entry",
                "id": int(reply_id),
                "ticketId": int(ticket_id),
                "label": _display_ticket_label(reply),
                "minutes": minutes,
                "hours": str(invoice_generator_service._minutes_to_hours(minutes)),
                "labourType": reply.get("labour_type_name") or reply.get("labour_type_code"),
                "action": "Clear the billable flag and remove any billed-time marker for this time entry",
            })
        if len(replies) < page_size:
            break
        offset += page_size
    return {
        "status": "ready" if items else "skipped",
        "command": "unbill_time_entries",
        "company_id": company_id,
        "summary": (
            f"{len(items)} billable time entr{'y' if len(items) == 1 else 'ies'} will be made non-billable."
            if items else "No billable time entries were found."
        ),
        "totals": {"timeEntryCount": len(items), "minutes": total_minutes},
        "items": items,
    }


async def unbill_time_entries(company_id: int | None = None, *, limit: int = 1000) -> dict[str, Any]:
    """Make billable time entries non-billable and clear billed-time markers."""
    preview = await preview_unbill_time_entries(company_id, limit=limit)
    reply_ids = [int(item["id"]) for item in preview.get("items", []) if item.get("id")]
    if not reply_ids:
        return preview
    removed_markers = await billed_time_repo.delete_entries_for_replies(reply_ids)
    updated = await tickets_repo.mark_replies_non_billable(reply_ids)
    return {
        **preview,
        "status": "succeeded" if updated else "skipped",
        "unbilledTimeEntries": updated,
        "removedBilledTimeMarkers": removed_markers,
        "summary": f"Made {updated} time entr{'y' if updated == 1 else 'ies'} non-billable and removed {removed_markers} billed-time marker{'s' if removed_markers != 1 else ''}.",
    }
