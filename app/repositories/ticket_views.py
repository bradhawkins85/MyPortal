from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

TicketViewRecord = dict[str, Any]


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _normalise_ticket_view(row: dict[str, Any]) -> TicketViewRecord:
    """Normalise a ticket view database record"""
    record = dict(row)
    for key in ("id", "user_id"):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    for key in ("created_at", "updated_at"):
        record[key] = _make_aware(record.get(key))
    
    # Parse JSON filters if present
    filters_value = record.get("filters")
    if filters_value:
        if isinstance(filters_value, str):
            try:
                record["filters"] = json.loads(filters_value)
            except json.JSONDecodeError:
                record["filters"] = None
        elif isinstance(filters_value, dict):
            record["filters"] = filters_value
    else:
        record["filters"] = None
    
    if "is_default" in record:
        record["is_default"] = bool(record.get("is_default"))
    
    return record


async def list_views_for_user(user_id: int) -> list[TicketViewRecord]:
    """List all saved views for a user"""
    query = """
        SELECT id, user_id, name, description, filters, grouping_field, 
               sort_field, sort_direction, is_default, created_at, updated_at
        FROM ticket_views
        WHERE user_id = %s
        ORDER BY is_default DESC, name ASC
    """
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (user_id,))
            rows = await cursor.fetchall()
            return [_normalise_ticket_view(dict(row)) for row in rows]


async def get_view(view_id: int, user_id: int) -> TicketViewRecord | None:
    """Get a specific saved view by ID for a user"""
    query = """
        SELECT id, user_id, name, description, filters, grouping_field, 
               sort_field, sort_direction, is_default, created_at, updated_at
        FROM ticket_views
        WHERE id = %s AND user_id = %s
    """
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (view_id, user_id))
            row = await cursor.fetchone()
            return _normalise_ticket_view(dict(row)) if row else None


async def get_default_view(user_id: int) -> TicketViewRecord | None:
    """Get the default view for a user"""
    query = """
        SELECT id, user_id, name, description, filters, grouping_field, 
               sort_field, sort_direction, is_default, created_at, updated_at
        FROM ticket_views
        WHERE user_id = %s AND is_default = 1
        LIMIT 1
    """
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (user_id,))
            row = await cursor.fetchone()
            return _normalise_ticket_view(dict(row)) if row else None


async def create_view(
    user_id: int,
    name: str,
    description: str | None = None,
    filters: dict | None = None,
    grouping_field: str | None = None,
    sort_field: str | None = None,
    sort_direction: str | None = None,
    is_default: bool = False,
) -> TicketViewRecord:
    """Create a new saved view"""
    # If setting as default, unset other defaults for this user
    if is_default:
        await _unset_default_views(user_id)
    
    filters_json = json.dumps(filters) if filters else None
    
    query = """
        INSERT INTO ticket_views 
        (user_id, name, description, filters, grouping_field, sort_field, 
         sort_direction, is_default)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                query,
                (
                    user_id,
                    name,
                    description,
                    filters_json,
                    grouping_field,
                    sort_field,
                    sort_direction,
                    is_default,
                ),
            )
            view_id = cursor.lastrowid
            await conn.commit()
    
    view = await get_view(view_id, user_id)
    if not view:
        raise RuntimeError("Failed to create ticket view")
    return view


async def update_view(
    view_id: int,
    user_id: int,
    **kwargs: Any,
) -> TicketViewRecord | None:
    """Update an existing saved view"""
    existing = await get_view(view_id, user_id)
    if not existing:
        return None
    
    # If setting as default, unset other defaults
    if kwargs.get("is_default"):
        await _unset_default_views(user_id)
    
    # Build update query dynamically
    allowed_fields = {
        "name", "description", "filters", "grouping_field",
        "sort_field", "sort_direction", "is_default"
    }
    updates = []
    params = []
    
    for key, value in kwargs.items():
        if key in allowed_fields:
            if key == "filters" and value is not None:
                value = json.dumps(value)
            updates.append(f"{key} = %s")
            params.append(value)
    
    if not updates:
        return existing
    
    params.extend([view_id, user_id])
    query = f"""
        UPDATE ticket_views
        SET {', '.join(updates)}
        WHERE id = %s AND user_id = %s
    """
    
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, tuple(params))
            await conn.commit()
    
    return await get_view(view_id, user_id)


async def delete_view(view_id: int, user_id: int) -> bool:
    """Delete a saved view"""
    query = "DELETE FROM ticket_views WHERE id = %s AND user_id = %s"
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (view_id, user_id))
            affected = cursor.rowcount
            await conn.commit()
            return affected > 0


async def _unset_default_views(user_id: int) -> None:
    """Unset all default views for a user"""
    query = "UPDATE ticket_views SET is_default = 0 WHERE user_id = %s AND is_default = 1"
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(query, (user_id,))
            await conn.commit()
