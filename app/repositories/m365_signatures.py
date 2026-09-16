from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from app.core.database import db


SignatureTemplateRecord = dict[str, Any]


async def _ensure_connection() -> None:
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


def _make_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _normalise_record(row: Mapping[str, Any] | None) -> SignatureTemplateRecord | None:
    if not row:
        return None
    record = dict(row)
    for key in ("id", "company_id", "created_by_user_id", "updated_by_user_id"):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    for key in ("created_at", "updated_at", "published_at", "disabled_at"):
        record[key] = _make_aware(record.get(key))
    for key in ("schedule_start_on", "schedule_end_on"):
        record[key] = _make_date(record.get(key))
    if "priority" in record and record["priority"] is not None:
        record["priority"] = int(record["priority"])
    if "is_default" in record:
        record["is_default"] = bool(record["is_default"])
    return record


async def list_templates(company_id: int) -> list[SignatureTemplateRecord]:
    await _ensure_connection()
    rows = await db.fetch_all(
        """
        SELECT id, company_id, slug, name, description, html_content, text_content, status,
               priority, is_default, schedule_start_on, schedule_end_on,
               created_by_user_id, updated_by_user_id, published_at, disabled_at,
               created_at, updated_at
        FROM m365_signature_templates
        WHERE company_id = %s
        ORDER BY updated_at DESC, id DESC
        """,
        (company_id,),
    )
    return [record for row in rows if (record := _normalise_record(row))]


async def get_template(company_id: int, template_id: int) -> SignatureTemplateRecord | None:
    await _ensure_connection()
    row = await db.fetch_one(
        """
        SELECT id, company_id, slug, name, description, html_content, text_content, status,
               priority, is_default, schedule_start_on, schedule_end_on,
               created_by_user_id, updated_by_user_id, published_at, disabled_at,
               created_at, updated_at
        FROM m365_signature_templates
        WHERE company_id = %s AND id = %s
        """,
        (company_id, template_id),
    )
    return _normalise_record(row)


async def get_template_by_slug(company_id: int, slug: str) -> SignatureTemplateRecord | None:
    await _ensure_connection()
    row = await db.fetch_one(
        """
        SELECT id, company_id, slug, name, description, html_content, text_content, status,
               priority, is_default, schedule_start_on, schedule_end_on,
               created_by_user_id, updated_by_user_id, published_at, disabled_at,
               created_at, updated_at
        FROM m365_signature_templates
        WHERE company_id = %s AND slug = %s
        """,
        (company_id, slug),
    )
    return _normalise_record(row)


async def create_template(
    *,
    company_id: int,
    slug: str,
    name: str,
    description: str | None,
    html_content: str,
    text_content: str,
    status: str,
    priority: int,
    is_default: bool,
    schedule_start_on: date | None,
    schedule_end_on: date | None,
    created_by_user_id: int | None,
    updated_by_user_id: int | None,
    published_at: datetime | None = None,
    disabled_at: datetime | None = None,
) -> SignatureTemplateRecord:
    await _ensure_connection()
    template_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO m365_signature_templates (
            company_id, slug, name, description, html_content, text_content, status,
            priority, is_default, schedule_start_on, schedule_end_on,
            created_by_user_id, updated_by_user_id, published_at, disabled_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            company_id,
            slug,
            name,
            description,
            html_content,
            text_content,
            status,
            priority,
            1 if is_default else 0,
            schedule_start_on,
            schedule_end_on,
            created_by_user_id,
            updated_by_user_id,
            published_at.replace(tzinfo=None) if isinstance(published_at, datetime) else published_at,
            disabled_at.replace(tzinfo=None) if isinstance(disabled_at, datetime) else disabled_at,
        ),
    )
    created = await get_template(company_id, int(template_id))
    if created:
        return created
    raise RuntimeError("Failed to create signature template")


async def update_template(
    company_id: int,
    template_id: int,
    **fields: Any,
) -> SignatureTemplateRecord | None:
    if not fields:
        return await get_template(company_id, template_id)
    await _ensure_connection()
    assignments: list[str] = []
    params: list[Any] = []
    allowed = {
        "slug",
        "name",
        "description",
        "html_content",
        "text_content",
        "status",
        "priority",
        "is_default",
        "schedule_start_on",
        "schedule_end_on",
        "created_by_user_id",
        "updated_by_user_id",
        "published_at",
        "disabled_at",
    }
    for key, value in fields.items():
        if key not in allowed:
            continue
        assignments.append(f"{key} = %s")
        if isinstance(value, datetime):
            params.append(value.replace(tzinfo=None))
        else:
            params.append(value)
    if not assignments:
        return await get_template(company_id, template_id)
    params.extend([company_id, template_id])
    await db.execute(
        f"""
        UPDATE m365_signature_templates
        SET {', '.join(assignments)}
        WHERE company_id = %s AND id = %s
        """,
        tuple(params),
    )
    return await get_template(company_id, template_id)


async def delete_template(company_id: int, template_id: int) -> bool:
    await _ensure_connection()
    result = await db.execute(
        "DELETE FROM m365_signature_templates WHERE company_id = %s AND id = %s",
        (company_id, template_id),
    )
    return bool(result)


async def clear_default_template(company_id: int, *, exclude_template_id: int | None = None) -> None:
    await _ensure_connection()
    query = "UPDATE m365_signature_templates SET is_default = 0 WHERE company_id = %s"
    params: list[Any] = [company_id]
    if exclude_template_id is not None:
        query += " AND id <> %s"
        params.append(exclude_template_id)
    await db.execute(query, tuple(params))


async def set_default_template(company_id: int, template_id: int) -> SignatureTemplateRecord | None:
    await clear_default_template(company_id, exclude_template_id=template_id)
    await update_template(company_id, template_id, is_default=True)
    return await get_template(company_id, template_id)
