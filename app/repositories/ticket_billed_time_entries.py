"""Repository for managing billed time entries in Xero."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db


async def create_billed_time_entry(
    *,
    ticket_id: int,
    reply_id: int,
    xero_invoice_number: str,
    minutes_billed: int,
    labour_type_id: int | None = None,
) -> dict[str, Any] | None:
    """Record a time entry as billed to prevent duplicate billing."""
    entry_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO ticket_billed_time_entries
            (ticket_id, reply_id, xero_invoice_number, minutes_billed, labour_type_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (ticket_id, reply_id, xero_invoice_number, minutes_billed, labour_type_id),
    )
    if entry_id:
        row = await db.fetch_one(
            "SELECT * FROM ticket_billed_time_entries WHERE id = %s", (entry_id,)
        )
        if row:
            return dict(row)
    return None


async def get_unbilled_reply_ids(ticket_id: int) -> set[int]:
    """Get reply IDs for a ticket that have not been billed yet."""
    rows = await db.fetch_all(
        """
        SELECT tr.id
        FROM ticket_replies tr
        WHERE tr.ticket_id = %s
          AND tr.is_billable = 1
          AND tr.minutes_spent IS NOT NULL
          AND tr.minutes_spent > 0
          AND NOT EXISTS (
              SELECT 1 FROM ticket_billed_time_entries bte
              WHERE bte.reply_id = tr.id
          )
        """,
        (ticket_id,),
    )
    return {int(row["id"]) for row in rows}


async def is_reply_billed(reply_id: int) -> bool:
    """Check if a reply has already been billed."""
    row = await db.fetch_one(
        "SELECT id FROM ticket_billed_time_entries WHERE reply_id = %s LIMIT 1",
        (reply_id,),
    )
    return row is not None


async def get_billed_entries_by_ticket(ticket_id: int) -> list[dict[str, Any]]:
    """Get all billed time entries for a ticket."""
    rows = await db.fetch_all(
        """
        SELECT bte.*, tr.minutes_spent, tr.author_id
        FROM ticket_billed_time_entries bte
        LEFT JOIN ticket_replies tr ON tr.id = bte.reply_id
        WHERE bte.ticket_id = %s
        ORDER BY bte.billed_at DESC
        """,
        (ticket_id,),
    )
    return [dict(row) for row in rows]


async def get_billed_entries_by_invoice(xero_invoice_number: str) -> list[dict[str, Any]]:
    """Get all billed time entries for a specific Xero invoice."""
    rows = await db.fetch_all(
        """
        SELECT bte.*, tr.minutes_spent, tr.author_id
        FROM ticket_billed_time_entries bte
        LEFT JOIN ticket_replies tr ON tr.id = bte.reply_id
        WHERE bte.xero_invoice_number = %s
        ORDER BY bte.billed_at DESC
        """,
        (xero_invoice_number,),
    )
    return [dict(row) for row in rows]
