from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

MessageTemplateRecord = dict[str, Any]


async def _ensure_connection() -> None:
    """Ensure the repository has an active database connection."""

    is_connected = getattr(db, "is_connected", None)
    if callable(is_connected):
        try:
            if is_connected():
                return
        except Exception:  # pragma: no cover - defensive guard
            pass
    connect = getattr(db, "connect", None)
    if not connect:
        return
    result = connect()
    if hasattr(result, "__await__"):
        await result


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _normalise_record(row: Mapping[str, Any] | None) -> MessageTemplateRecord | None:
    if not row:
        return None
    record = dict(row)
    for key in ("id",):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    for key in ("created_at", "updated_at"):
        record[key] = _make_aware(record.get(key))
    return record


async def list_templates(
    *,
    search: str | None = None,
    content_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[MessageTemplateRecord]:
    await _ensure_connection()
    where_clauses: list[str] = []
    params: list[Any] = []
    if search:
        where_clauses.append("(slug LIKE %s OR name LIKE %s)")
        like = f"%{search}%"
        params.extend([like, like])
    if content_type:
        where_clauses.append("content_type = %s")
        params.append(content_type)
    where = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    params.extend([limit, offset])
    rows = await db.fetch_all(
        f"""
        SELECT id, slug, name, description, content_type, content, created_at, updated_at
        FROM message_templates
        {where}
        ORDER BY updated_at DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params),
    )
    return [record for row in rows if (record := _normalise_record(row))]


async def get_template(template_id: int) -> MessageTemplateRecord | None:
    await _ensure_connection()
    row = await db.fetch_one(
        "SELECT id, slug, name, description, content_type, content, created_at, updated_at FROM message_templates WHERE id = %s",
        (template_id,),
    )
    return _normalise_record(row)


async def get_template_by_slug(slug: str) -> MessageTemplateRecord | None:
    await _ensure_connection()
    row = await db.fetch_one(
        "SELECT id, slug, name, description, content_type, content, created_at, updated_at FROM message_templates WHERE slug = %s",
        (slug,),
    )
    return _normalise_record(row)


async def create_template(
    *,
    slug: str,
    name: str,
    description: str | None,
    content_type: str,
    content: str,
) -> MessageTemplateRecord:
    await _ensure_connection()
    template_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO message_templates (
            slug,
            name,
            description,
            content_type,
            content
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (slug, name, description, content_type, content),
    )
    created = await get_template(template_id)
    if created:
        return created
    return {
        "id": template_id,
        "slug": slug,
        "name": name,
        "description": description,
        "content_type": content_type,
        "content": content,
        "created_at": None,
        "updated_at": None,
    }


async def update_template(template_id: int, **fields: Any) -> MessageTemplateRecord | None:
    if not fields:
        return await get_template(template_id)
    await _ensure_connection()
    assignments: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key not in {"slug", "name", "description", "content_type", "content"}:
            continue
        assignments.append(f"{key} = %s")
        params.append(value)
    if not assignments:
        return await get_template(template_id)
    params.append(template_id)
    await db.execute(
        f"UPDATE message_templates SET {', '.join(assignments)} WHERE id = %s",
        tuple(params),
    )
    return await get_template(template_id)


async def delete_template(template_id: int) -> None:
    await _ensure_connection()
    await db.execute("DELETE FROM message_templates WHERE id = %s", (template_id,))
