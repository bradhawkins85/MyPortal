from __future__ import annotations

from typing import Any

from app.core.database import db


async def get_schedule(schedule_id: int) -> dict[str, Any] | None:
    """Get a schedule by ID."""
    row = await db.fetch_one(
        "SELECT * FROM booking_schedules WHERE id = %s",
        (schedule_id,)
    )
    return dict(row) if row else None


async def list_schedules(user_id: int) -> list[dict[str, Any]]:
    """List schedules for a user."""
    rows = await db.fetch_all(
        "SELECT * FROM booking_schedules WHERE user_id = %s ORDER BY is_default DESC, id",
        (user_id,)
    )
    return [dict(row) for row in rows]


async def create_schedule(
    user_id: int,
    name: str,
    timezone: str = "UTC",
    is_default: bool = False
) -> dict[str, Any]:
    """Create a new schedule."""
    await db.execute(
        """
        INSERT INTO booking_schedules (user_id, name, timezone, is_default)
        VALUES (%s, %s, %s, %s)
        """,
        (user_id, name, timezone, 1 if is_default else 0)
    )

    row = await db.fetch_one(
        "SELECT * FROM booking_schedules WHERE user_id = %s AND name = %s ORDER BY id DESC LIMIT 1",
        (user_id, name)
    )
    return dict(row) if row else {}


async def update_schedule(schedule_id: int, **updates: Any) -> dict[str, Any]:
    """Update a schedule."""
    if not updates:
        schedule = await get_schedule(schedule_id)
        if not schedule:
            raise ValueError("Schedule not found")
        return schedule

    columns = ", ".join(f"{column} = %s" for column in updates.keys())
    params = list(updates.values()) + [schedule_id]

    await db.execute(
        f"UPDATE booking_schedules SET {columns} WHERE id = %s",
        tuple(params)
    )

    updated = await get_schedule(schedule_id)
    if not updated:
        raise ValueError("Schedule not found after update")
    return updated


async def delete_schedule(schedule_id: int) -> None:
    """Delete a schedule."""
    await db.execute(
        "DELETE FROM booking_schedules WHERE id = %s",
        (schedule_id,)
    )


async def list_availability(schedule_id: int) -> list[dict[str, Any]]:
    """List availability rules for a schedule."""
    rows = await db.fetch_all(
        "SELECT * FROM booking_availability WHERE schedule_id = %s ORDER BY day_of_week, start_time",
        (schedule_id,)
    )
    return [dict(row) for row in rows]


async def create_availability(
    schedule_id: int,
    day_of_week: int,
    start_time: str,
    end_time: str
) -> dict[str, Any]:
    """Create an availability rule."""
    await db.execute(
        """
        INSERT INTO booking_availability (schedule_id, day_of_week, start_time, end_time)
        VALUES (%s, %s, %s, %s)
        """,
        (schedule_id, day_of_week, start_time, end_time)
    )

    row = await db.fetch_one(
        "SELECT * FROM booking_availability WHERE schedule_id = %s ORDER BY id DESC LIMIT 1",
        (schedule_id,)
    )
    return dict(row) if row else {}


async def delete_availability(availability_id: int) -> None:
    """Delete an availability rule."""
    await db.execute(
        "DELETE FROM booking_availability WHERE id = %s",
        (availability_id,)
    )


async def list_date_overrides(schedule_id: int) -> list[dict[str, Any]]:
    """List date overrides for a schedule."""
    rows = await db.fetch_all(
        "SELECT * FROM booking_date_overrides WHERE schedule_id = %s ORDER BY override_date",
        (schedule_id,)
    )
    return [dict(row) for row in rows]


async def create_date_override(
    schedule_id: int,
    override_date: str,
    is_available: bool = False,
    start_time: str | None = None,
    end_time: str | None = None,
    reason: str | None = None
) -> dict[str, Any]:
    """Create a date override."""
    await db.execute(
        """
        INSERT INTO booking_date_overrides 
        (schedule_id, override_date, is_available, start_time, end_time, reason)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (schedule_id, override_date, 1 if is_available else 0, start_time, end_time, reason)
    )

    row = await db.fetch_one(
        "SELECT * FROM booking_date_overrides WHERE schedule_id = %s ORDER BY id DESC LIMIT 1",
        (schedule_id,)
    )
    return dict(row) if row else {}


async def delete_date_override(override_id: int) -> None:
    """Delete a date override."""
    await db.execute(
        "DELETE FROM booking_date_overrides WHERE id = %s",
        (override_id,)
    )
