from __future__ import annotations

import json
from typing import Any

from app.core.database import db


async def get_event_type(event_type_id: int) -> dict[str, Any] | None:
    """Get an event type by ID."""
    row = await db.fetch_one(
        "SELECT * FROM booking_event_types WHERE id = %s",
        (event_type_id,)
    )
    if row:
        result = dict(row)
        if result.get("metadata") and isinstance(result["metadata"], str):
            result["metadata"] = json.loads(result["metadata"])
        return result
    return None


async def get_event_type_by_slug(user_id: int, slug: str) -> dict[str, Any] | None:
    """Get an event type by user ID and slug."""
    row = await db.fetch_one(
        "SELECT * FROM booking_event_types WHERE user_id = %s AND slug = %s",
        (user_id, slug)
    )
    if row:
        result = dict(row)
        if result.get("metadata") and isinstance(result["metadata"], str):
            result["metadata"] = json.loads(result["metadata"])
        return result
    return None


async def list_event_types(
    user_id: int | None = None,
    is_active: bool | None = None,
    limit: int = 100
) -> list[dict[str, Any]]:
    """List event types with optional filters."""
    conditions = []
    params = []

    if user_id is not None:
        conditions.append("user_id = %s")
        params.append(user_id)

    if is_active is not None:
        conditions.append("is_active = %s")
        params.append(1 if is_active else 0)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    rows = await db.fetch_all(
        f"SELECT * FROM booking_event_types {where_clause} ORDER BY id DESC LIMIT %s",
        tuple(params)
    )

    results = []
    for row in rows:
        result = dict(row)
        if result.get("metadata") and isinstance(result["metadata"], str):
            result["metadata"] = json.loads(result["metadata"])
        results.append(result)
    return results


async def create_event_type(
    user_id: int,
    slug: str,
    title: str,
    duration_minutes: int = 30,
    **kwargs: Any
) -> dict[str, Any]:
    """Create a new event type."""
    metadata = kwargs.pop("metadata", None)
    metadata_json = json.dumps(metadata) if metadata else None

    columns = ["user_id", "slug", "title", "duration_minutes"]
    values = [user_id, slug, title, duration_minutes]

    for key, value in kwargs.items():
        columns.append(key)
        values.append(value)

    if metadata_json:
        columns.append("metadata")
        values.append(metadata_json)

    placeholders = ", ".join(["%s"] * len(columns))
    column_names = ", ".join(columns)

    await db.execute(
        f"INSERT INTO booking_event_types ({column_names}) VALUES ({placeholders})",
        tuple(values)
    )

    return await get_event_type_by_slug(user_id, slug)


async def update_event_type(event_type_id: int, **updates: Any) -> dict[str, Any]:
    """Update an event type."""
    if not updates:
        event_type = await get_event_type(event_type_id)
        if not event_type:
            raise ValueError("Event type not found")
        return event_type

    if "metadata" in updates and updates["metadata"] is not None:
        updates["metadata"] = json.dumps(updates["metadata"])

    columns = ", ".join(f"{column} = %s" for column in updates.keys())
    params = list(updates.values()) + [event_type_id]

    await db.execute(
        f"UPDATE booking_event_types SET {columns} WHERE id = %s",
        tuple(params)
    )

    updated = await get_event_type(event_type_id)
    if not updated:
        raise ValueError("Event type not found after update")
    return updated


async def delete_event_type(event_type_id: int) -> None:
    """Delete an event type."""
    await db.execute(
        "DELETE FROM booking_event_types WHERE id = %s",
        (event_type_id,)
    )
