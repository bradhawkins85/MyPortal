from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db


def _serialise_metadata(metadata: dict[str, Any] | None) -> str | None:
    if metadata is None:
        return None
    return json.dumps(metadata)


def _deserialise_metadata(value: Any) -> dict[str, Any] | None:
    if value in (None, "", b""):
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
    if isinstance(value, dict):
        return value
    return {"raw": value}


async def list_notifications(
    *,
    user_id: int | None = None,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if user_id is not None:
        clauses.append("(user_id = %s OR user_id IS NULL)")
        params.append(user_id)
    if unread_only:
        clauses.append("read_at IS NULL")
    sql = (
        "SELECT id, user_id, event_type, message, metadata, created_at, read_at "
        f"FROM notifications WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
    )
    params.extend([limit, offset])
    rows = await db.fetch_all(sql, tuple(params))
    result: list[dict[str, Any]] = []
    for row in rows:
        metadata = _deserialise_metadata(row.get("metadata"))
        row["metadata"] = metadata
        result.append(row)
    return result


async def create_notification(
    *,
    event_type: str,
    message: str,
    user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "event_type": event_type,
        "message": message,
        "user_id": user_id,
        "metadata": _serialise_metadata(metadata),
    }
    await db.execute(
        """
        INSERT INTO notifications (event_type, message, user_id, metadata)
        VALUES (%(event_type)s, %(message)s, %(user_id)s, %(metadata)s)
        """,
        payload,
    )
    row = await db.fetch_one(
        """
        SELECT id, user_id, event_type, message, metadata, created_at, read_at
        FROM notifications
        WHERE event_type = %(event_type)s AND message = %(message)s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        payload,
    )
    if not row:
        raise RuntimeError("Failed to persist notification")
    row["metadata"] = _deserialise_metadata(row.get("metadata"))
    return row


async def mark_read(notification_id: int) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    await db.execute(
        "UPDATE notifications SET read_at = %s WHERE id = %s",
        (now, notification_id),
    )
    return await get_notification(notification_id)


async def get_notification(notification_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, user_id, event_type, message, metadata, created_at, read_at FROM notifications WHERE id = %s",
        (notification_id,),
    )
    if row:
        row["metadata"] = _deserialise_metadata(row.get("metadata"))
    return row
