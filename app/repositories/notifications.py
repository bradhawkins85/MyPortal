from __future__ import annotations

import json
from collections.abc import Sequence
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


def _normalise_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None)


def _build_filters(
    *,
    user_id: int | None = None,
    read_state: str | None = None,
    event_types: Sequence[str] | None = None,
    search: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> tuple[list[str], list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []

    if user_id is not None:
        clauses.append("(user_id = %s OR user_id IS NULL)")
        params.append(user_id)

    if read_state == "unread":
        clauses.append("read_at IS NULL")
    elif read_state == "read":
        clauses.append("read_at IS NOT NULL")

    if event_types:
        filtered = [event_type for event_type in event_types if event_type]
        if filtered:
            placeholders = ", ".join(["%s"] * len(filtered))
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(filtered)

    if search:
        like = f"%{search.lower()}%"
        clauses.append(
            "(LOWER(message) LIKE %s OR LOWER(COALESCE(CAST(metadata AS CHAR), '')) LIKE %s)"
        )
        params.extend([like, like])

    start = _normalise_datetime(created_from)
    if start is not None:
        clauses.append("created_at >= %s")
        params.append(start)

    end = _normalise_datetime(created_to)
    if end is not None:
        clauses.append("created_at < %s")
        params.append(end)

    return clauses, params


async def list_notifications(
    *,
    user_id: int | None = None,
    read_state: str | None = None,
    event_types: Sequence[str] | None = None,
    search: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sort_by: str = "created_at",
    sort_direction: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses, params = _build_filters(
        user_id=user_id,
        read_state=read_state,
        event_types=event_types,
        search=search,
        created_from=created_from,
        created_to=created_to,
    )

    sort_columns = {
        "created_at": "created_at",
        "event_type": "event_type",
        "read_at": "read_at",
    }
    column = sort_columns.get(sort_by, "created_at")
    order = "ASC" if sort_direction.lower() == "asc" else "DESC"

    sql = (
        "SELECT id, user_id, event_type, message, metadata, created_at, read_at "
        f"FROM notifications WHERE {' AND '.join(clauses)} "
        f"ORDER BY {column} {order}, id DESC LIMIT %s OFFSET %s"
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


async def mark_read_bulk(notification_ids: Sequence[int]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    unique_ids = [int(notification_id) for notification_id in dict.fromkeys(notification_ids)]
    if not unique_ids:
        return []

    placeholders = ", ".join(["%s"] * len(unique_ids))
    await db.execute(
        f"UPDATE notifications SET read_at = %s WHERE id IN ({placeholders})",
        tuple([now] + unique_ids),
    )
    rows = await db.fetch_all(
        f"SELECT id, user_id, event_type, message, metadata, created_at, read_at "
        f"FROM notifications WHERE id IN ({placeholders})",
        tuple(unique_ids),
    )
    mapped = {row["id"]: row for row in rows}
    ordered: list[dict[str, Any]] = []
    for notification_id in unique_ids:
        row = mapped.get(notification_id)
        if not row:
            continue
        row["metadata"] = _deserialise_metadata(row.get("metadata"))
        ordered.append(row)
    return ordered


async def get_notification(notification_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, user_id, event_type, message, metadata, created_at, read_at FROM notifications WHERE id = %s",
        (notification_id,),
    )
    if row:
        row["metadata"] = _deserialise_metadata(row.get("metadata"))
    return row


async def count_notifications(
    *,
    user_id: int | None = None,
    read_state: str | None = None,
    event_types: Sequence[str] | None = None,
    search: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> int:
    clauses, params = _build_filters(
        user_id=user_id,
        read_state=read_state,
        event_types=event_types,
        search=search,
        created_from=created_from,
        created_to=created_to,
    )
    sql = f"SELECT COUNT(*) AS count FROM notifications WHERE {' AND '.join(clauses)}"
    row = await db.fetch_one(sql, tuple(params))
    if not row:
        return 0
    count = row.get("count", 0)
    try:
        return int(count)
    except (TypeError, ValueError):
        return 0


async def list_event_types(*, user_id: int | None = None) -> list[str]:
    clauses = ["1=1"]
    params: list[Any] = []
    if user_id is not None:
        clauses.append("(user_id = %s OR user_id IS NULL)")
        params.append(user_id)
    sql = f"SELECT DISTINCT event_type FROM notifications WHERE {' AND '.join(clauses)} ORDER BY event_type"
    rows = await db.fetch_all(sql, tuple(params))
    values: list[str] = []
    for row in rows:
        event_type = row.get("event_type")
        if isinstance(event_type, str):
            values.append(event_type)
    return values
