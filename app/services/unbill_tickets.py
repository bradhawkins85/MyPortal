from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.repositories import ticket_billed_time_entries as billed_time_repo
from app.repositories import tickets as tickets_repo

_DATE_FORMAT = "%Y%m%d"
_DEFAULT_TICKETS_PAGE_SIZE = 1000


def parse_unbill_cutoff_date(value: str) -> datetime:
    """Parse a yyyyMMdd cutoff date as the start of that UTC day."""
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("Enter a cutoff date in yyyyMMdd format.")
    if len(cleaned) != 8 or not cleaned.isdigit():
        raise ValueError("Cutoff date must use yyyyMMdd format, for example 20240131.")
    try:
        parsed = datetime.strptime(cleaned, _DATE_FORMAT)
    except ValueError as exc:
        raise ValueError("Enter a valid cutoff date in yyyyMMdd format.") from exc
    return parsed.replace(tzinfo=timezone.utc)


def _ticket_label(ticket: Mapping[str, Any]) -> str:
    ticket_number = str(ticket.get("ticket_number") or "").strip()
    subject = str(ticket.get("subject") or "").strip()
    if ticket_number and subject:
        return f"Ticket #{ticket_number}: {subject}"
    if subject:
        return subject
    return f"Ticket #{ticket.get('id')}"


async def preview_unbill_tickets(
    cutoff_date: datetime, *, limit: int = _DEFAULT_TICKETS_PAGE_SIZE
) -> dict[str, Any]:
    """Preview billed tickets created before the UTC cutoff date.

    Billed tickets are read in pages so old tickets are not skipped when the
    matching set is larger than a single query limit.
    """
    page_size = max(1, int(limit or _DEFAULT_TICKETS_PAGE_SIZE))
    offset = 0
    tickets: list[Mapping[str, Any]] = []
    while True:
        page = await tickets_repo.list_billed_tickets_older_than(
            cutoff_date,
            limit=page_size,
            offset=offset,
        )
        if not page:
            break
        tickets.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    items = [
        {
            "type": "ticket",
            "id": int(ticket["id"]),
            "label": _ticket_label(ticket),
            "createdAt": ticket.get("created_at"),
            "billedAt": ticket.get("billed_at"),
            "invoiceNumber": ticket.get("xero_invoice_number"),
            "action": "Remove ticket billing markers and billed time-entry records",
        }
        for ticket in tickets
        if ticket.get("id")
    ]
    return {
        "status": "ready" if items else "skipped",
        "command": "unbill_tickets",
        "cutoffDate": cutoff_date.date().isoformat(),
        "summary": (
            f"{len(items)} billed ticket{'s' if len(items) != 1 else ''} created before "
            f"{cutoff_date.date().isoformat()} will be un-billed."
            if items else f"No billed tickets created before {cutoff_date.date().isoformat()} were found."
        ),
        "totals": {"ticketCount": len(items)},
        "items": items,
    }


async def unbill_tickets_older_than(cutoff_date: datetime) -> dict[str, Any]:
    """Remove billing markers from billed tickets created before the cutoff date."""
    preview = await preview_unbill_tickets(cutoff_date)
    ticket_ids = [int(item["id"]) for item in preview.get("items", []) if item.get("id")]
    if not ticket_ids:
        return preview
    deleted_entries = await billed_time_repo.delete_entries_for_tickets(ticket_ids)
    cleared_tickets = await tickets_repo.clear_ticket_billing_fields(ticket_ids)
    return {
        **preview,
        "status": "succeeded" if cleared_tickets else "skipped",
        "unbilledTickets": cleared_tickets,
        "deletedBilledTimeEntries": deleted_entries,
        "summary": (
            f"Un-billed {cleared_tickets} ticket{'s' if cleared_tickets != 1 else ''} and removed "
            f"{deleted_entries} billed time entr{'ies' if deleted_entries != 1 else 'y'}."
        ),
    }
