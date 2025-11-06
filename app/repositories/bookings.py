from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

from app.core.database import db


def generate_booking_uid() -> str:
    """Generate a unique booking identifier with bk_ prefix."""
    return f"bk_{secrets.token_hex(12)}"


async def get_booking(booking_id: int) -> dict[str, Any] | None:
    """Get a booking by ID."""
    row = await db.fetch_one(
        "SELECT * FROM bookings WHERE id = %s",
        (booking_id,)
    )
    if row:
        result = dict(row)
        if result.get("metadata") and isinstance(result["metadata"], str):
            result["metadata"] = json.loads(result["metadata"])
        return result
    return None


async def get_booking_by_uid(uid: str) -> dict[str, Any] | None:
    """Get a booking by UID."""
    row = await db.fetch_one(
        "SELECT * FROM bookings WHERE uid = %s",
        (uid,)
    )
    if row:
        result = dict(row)
        if result.get("metadata") and isinstance(result["metadata"], str):
            result["metadata"] = json.loads(result["metadata"])
        return result
    return None


async def list_bookings(
    event_type_id: int | None = None,
    host_user_id: int | None = None,
    status: str | None = None,
    start_after: datetime | None = None,
    start_before: datetime | None = None,
    limit: int = 100,
    offset: int = 0
) -> list[dict[str, Any]]:
    """List bookings with optional filters."""
    conditions = []
    params = []

    if event_type_id is not None:
        conditions.append("event_type_id = %s")
        params.append(event_type_id)

    if host_user_id is not None:
        conditions.append("host_user_id = %s")
        params.append(host_user_id)

    if status is not None:
        conditions.append("status = %s")
        params.append(status)

    if start_after is not None:
        conditions.append("start_time >= %s")
        params.append(start_after)

    if start_before is not None:
        conditions.append("start_time < %s")
        params.append(start_before)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    rows = await db.fetch_all(
        f"SELECT * FROM bookings {where_clause} ORDER BY start_time DESC LIMIT %s OFFSET %s",
        tuple(params)
    )

    results = []
    for row in rows:
        result = dict(row)
        if result.get("metadata") and isinstance(result["metadata"], str):
            result["metadata"] = json.loads(result["metadata"])
        results.append(result)
    return results


async def create_booking(
    event_type_id: int,
    host_user_id: int,
    title: str,
    start_time: datetime,
    end_time: datetime,
    status: str = "pending",
    **kwargs: Any
) -> dict[str, Any]:
    """Create a new booking."""
    uid = generate_booking_uid()
    metadata = kwargs.pop("metadata", None)
    metadata_json = json.dumps(metadata) if metadata else None

    columns = ["uid", "event_type_id", "host_user_id", "title", "start_time", "end_time", "status"]
    values = [uid, event_type_id, host_user_id, title, start_time, end_time, status]

    for key, value in kwargs.items():
        if key not in ("metadata",):
            columns.append(key)
            values.append(value)

    if metadata_json:
        columns.append("metadata")
        values.append(metadata_json)

    placeholders = ", ".join(["%s"] * len(columns))
    column_names = ", ".join(columns)

    await db.execute(
        f"INSERT INTO bookings ({column_names}) VALUES ({placeholders})",
        tuple(values)
    )

    return await get_booking_by_uid(uid)


async def update_booking(booking_id: int, **updates: Any) -> dict[str, Any]:
    """Update a booking."""
    if not updates:
        booking = await get_booking(booking_id)
        if not booking:
            raise ValueError("Booking not found")
        return booking

    if "metadata" in updates and updates["metadata"] is not None:
        updates["metadata"] = json.dumps(updates["metadata"])

    columns = ", ".join(f"{column} = %s" for column in updates.keys())
    params = list(updates.values()) + [booking_id]

    await db.execute(
        f"UPDATE bookings SET {columns} WHERE id = %s",
        tuple(params)
    )

    updated = await get_booking(booking_id)
    if not updated:
        raise ValueError("Booking not found after update")
    return updated


async def delete_booking(booking_id: int) -> None:
    """Delete a booking."""
    await db.execute(
        "DELETE FROM bookings WHERE id = %s",
        (booking_id,)
    )


async def list_attendees(booking_id: int) -> list[dict[str, Any]]:
    """List attendees for a booking."""
    rows = await db.fetch_all(
        "SELECT * FROM booking_attendees WHERE booking_id = %s ORDER BY id",
        (booking_id,)
    )

    results = []
    for row in rows:
        result = dict(row)
        if result.get("metadata") and isinstance(result["metadata"], str):
            result["metadata"] = json.loads(result["metadata"])
        results.append(result)
    return results


async def create_attendee(
    booking_id: int,
    name: str,
    email: str,
    timezone: str = "UTC",
    notes: str | None = None,
    metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create a booking attendee."""
    metadata_json = json.dumps(metadata) if metadata else None

    await db.execute(
        """
        INSERT INTO booking_attendees (booking_id, name, email, timezone, notes, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (booking_id, name, email, timezone, notes, metadata_json)
    )

    row = await db.fetch_one(
        "SELECT * FROM booking_attendees WHERE booking_id = %s ORDER BY id DESC LIMIT 1",
        (booking_id,)
    )

    if row:
        result = dict(row)
        if result.get("metadata") and isinstance(result["metadata"], str):
            result["metadata"] = json.loads(result["metadata"])
        return result
    return {}


async def delete_attendee(attendee_id: int) -> None:
    """Delete an attendee."""
    await db.execute(
        "DELETE FROM booking_attendees WHERE id = %s",
        (attendee_id,)
    )
