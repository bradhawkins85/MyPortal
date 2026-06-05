from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_excluded_event_types(user_id: int) -> list[str]:
    rows = await db.fetch_all(
        """
        SELECT event_type
        FROM notification_exclusions
        WHERE user_id = %s
        ORDER BY event_type
        """,
        (user_id,),
    )
    values: list[str] = []
    for row in rows:
        event_type = row.get("event_type")
        if isinstance(event_type, str):
            values.append(event_type)
    return values


async def is_event_type_excluded(user_id: int, event_type: str) -> bool:
    row = await db.fetch_one(
        """
        SELECT 1 AS is_excluded
        FROM notification_exclusions
        WHERE user_id = %s AND event_type = %s
        LIMIT 1
        """,
        (user_id, event_type),
    )
    return bool(row)


async def exclude_event_type(user_id: int, event_type: str) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO notification_exclusions (user_id, event_type)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE
            excluded_at = CURRENT_TIMESTAMP
        """,
        (user_id, event_type),
    )
    return {"event_type": event_type, "is_excluded": True}


async def undo_exclude_event_type(user_id: int, event_type: str) -> dict[str, Any]:
    await db.execute(
        """
        DELETE FROM notification_exclusions
        WHERE user_id = %s AND event_type = %s
        """,
        (user_id, event_type),
    )
    return {"event_type": event_type, "is_excluded": False}
