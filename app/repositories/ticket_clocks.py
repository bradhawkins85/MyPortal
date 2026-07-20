"""Persistence helpers for technician ticket-page clocks."""

from __future__ import annotations

from typing import Any

from app.core.database import db


async def start_clock(ticket_id: int, user_id: int) -> int:
    return await db.execute_returning_lastrowid(
        "INSERT INTO ticket_page_clocks (ticket_id, user_id) VALUES (%s, %s)",
        (ticket_id, user_id),
    )


async def touch_clock(clock_id: int, ticket_id: int, user_id: int) -> bool:
    affected = await db.execute(
        """
        UPDATE ticket_page_clocks
        SET last_seen_at = CURRENT_TIMESTAMP
        WHERE id = %s AND ticket_id = %s AND user_id = %s AND ended_at IS NULL
        """,
        (clock_id, ticket_id, user_id),
    )
    return bool(affected)


async def stop_clock(clock_id: int, ticket_id: int, user_id: int) -> bool:
    affected = await db.execute(
        """
        UPDATE ticket_page_clocks
        SET ended_at = CURRENT_TIMESTAMP, last_seen_at = CURRENT_TIMESTAMP
        WHERE id = %s AND ticket_id = %s AND user_id = %s AND ended_at IS NULL
        """,
        (clock_id, ticket_id, user_id),
    )
    return bool(affected)


async def list_clocks(ticket_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT c.id, c.started_at, c.last_seen_at, c.ended_at,
               u.email AS user_email,
               NULLIF(TRIM(CONCAT(COALESCE(u.first_name, ''), ' ', COALESCE(u.last_name, ''))), '') AS user_display_name
        FROM ticket_page_clocks c
        LEFT JOIN users u ON u.id = c.user_id
        WHERE c.ticket_id = %s
        ORDER BY c.started_at DESC, c.id DESC
        """,
        (ticket_id,),
    )
    return [dict(row) for row in rows]
