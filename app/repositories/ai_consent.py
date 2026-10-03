"""Lookups used to remove an opted-out user's content from the AI search index."""
from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_requested_ticket_ids(user_id: int) -> list[int]:
    rows = await db.fetch_all(
        "SELECT id FROM tickets WHERE requester_id = %s",
        (int(user_id),),
    )
    return [int(row["id"]) for row in rows or []]


async def list_ticket_replies_involving_user(user_id: int) -> list[dict[str, Any]]:
    """Return ticket/reply id pairs the user wrote or that sit on the user's tickets."""

    rows = await db.fetch_all(
        """
        SELECT tr.ticket_id, tr.id AS reply_id, tr.author_id, t.requester_id
        FROM ticket_replies tr
        INNER JOIN tickets t ON t.id = tr.ticket_id
        WHERE tr.author_id = %s OR t.requester_id = %s
        """,
        (int(user_id), int(user_id)),
    )
    return [dict(row) for row in rows or []]


async def list_created_chat_room_ids(user_id: int) -> list[int]:
    rows = await db.fetch_all(
        "SELECT id FROM chat_rooms WHERE created_by_user_id = %s",
        (int(user_id),),
    )
    return [int(row["id"]) for row in rows or []]


async def get_ticket_requester(ticket_id: int) -> dict[str, Any] | None:
    """Return the requester columns of a ticket, or None when unavailable."""

    if not db.is_connected():
        return None
    row = await db.fetch_one(
        "SELECT id, requester_id, requester_staff_id FROM tickets WHERE id = %s",
        (int(ticket_id),),
    )
    return dict(row) if row else None
