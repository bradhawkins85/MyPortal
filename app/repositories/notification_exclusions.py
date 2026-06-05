from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_exclusions(user_id: int) -> list[dict[str, Any]]:
    """Return all exclusion rules for a user, ordered by event type and pattern."""
    rows = await db.fetch_all(
        """
        SELECT id, event_type, message_pattern, excluded_at
        FROM notification_exclusions
        WHERE user_id = %s
        ORDER BY event_type, message_pattern
        """,
        (user_id,),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": row.get("id"),
                "event_type": row.get("event_type") or "",
                "message_pattern": row.get("message_pattern") or "",
                "excluded_at": row.get("excluded_at"),
            }
        )
    return result


async def list_excluded_event_types(user_id: int) -> list[str]:
    """Return distinct event types that have at least one exclusion rule (pattern-less)."""
    rows = await db.fetch_all(
        """
        SELECT DISTINCT event_type
        FROM notification_exclusions
        WHERE user_id = %s AND message_pattern = ''
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


async def is_notification_excluded(user_id: int, event_type: str, message: str) -> bool:
    """Check whether a notification is excluded for the given user.

    A notification is excluded if there is a matching rule:
    - A rule with ``message_pattern = ''`` and matching ``event_type`` excludes all
      notifications of that event type.
    - A rule with a non-empty ``message_pattern`` matches when the notification
      message starts with that pattern (case-sensitive prefix match).
    """
    row = await db.fetch_one(
        """
        SELECT 1 AS is_excluded
        FROM notification_exclusions
        WHERE user_id = %s
          AND event_type = %s
          AND (
            message_pattern = ''
            OR %s LIKE CONCAT(message_pattern, '%%')
          )
        LIMIT 1
        """,
        (user_id, event_type, message),
    )
    return bool(row)


async def is_event_type_excluded(user_id: int, event_type: str) -> bool:
    """Check whether the event type itself (pattern-less) is excluded."""
    row = await db.fetch_one(
        """
        SELECT 1 AS is_excluded
        FROM notification_exclusions
        WHERE user_id = %s AND event_type = %s AND message_pattern = ''
        LIMIT 1
        """,
        (user_id, event_type),
    )
    return bool(row)


async def exclude_notification(
    user_id: int, event_type: str, message_pattern: str = ""
) -> dict[str, Any]:
    """Add an exclusion rule for the given event type and optional message pattern."""
    await db.execute(
        """
        INSERT INTO notification_exclusions (user_id, event_type, message_pattern)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            excluded_at = CURRENT_TIMESTAMP
        """,
        (user_id, event_type, message_pattern),
    )
    return {"event_type": event_type, "message_pattern": message_pattern, "is_excluded": True}


async def exclude_event_type(user_id: int, event_type: str) -> dict[str, Any]:
    """Exclude all notifications of the given event type (no message filter)."""
    return await exclude_notification(user_id, event_type, message_pattern="")


async def delete_exclusion(user_id: int, exclusion_id: int) -> bool:
    """Remove a specific exclusion rule by its primary key. Returns True if a row was deleted."""
    result = await db.execute(
        """
        DELETE FROM notification_exclusions
        WHERE id = %s AND user_id = %s
        """,
        (exclusion_id, user_id),
    )
    deleted = int(result or 0)
    return deleted > 0


async def undo_exclude_event_type(user_id: int, event_type: str) -> dict[str, Any]:
    """Remove all exclusion rules for the given event type (kept for backward compatibility)."""
    await db.execute(
        """
        DELETE FROM notification_exclusions
        WHERE user_id = %s AND event_type = %s
        """,
        (user_id, event_type),
    )
    return {"event_type": event_type, "is_excluded": False}
