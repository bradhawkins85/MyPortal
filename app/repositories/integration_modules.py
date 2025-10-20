from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

ModuleRecord = dict[str, Any]


def _serialise(value: Any) -> str:
    if value is None:
        return json.dumps({})
    return json.dumps(value, default=str)


def _deserialise(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _normalise_module(row: dict[str, Any]) -> ModuleRecord:
    record = dict(row)
    for key in ("id",):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    for key in ("enabled",):
        if key in record:
            record[key] = bool(record[key])
    for key in ("created_at", "updated_at"):
        record[key] = _make_aware(record.get(key))
    record["settings"] = _deserialise(record.get("settings"))
    return record


async def list_modules() -> list[ModuleRecord]:
    rows = await db.fetch_all(
        """
        SELECT *
        FROM integration_modules
        ORDER BY name ASC
        """
    )
    return [_normalise_module(row) for row in rows]


async def get_module(slug: str) -> ModuleRecord | None:
    row = await db.fetch_one(
        "SELECT * FROM integration_modules WHERE slug = %s",
        (slug,),
    )
    return _normalise_module(row) if row else None


async def upsert_module(
    *,
    slug: str,
    name: str,
    description: str | None,
    icon: str | None,
    enabled: bool,
    settings: Any,
) -> ModuleRecord:
    await db.execute(
        """
        INSERT INTO integration_modules (slug, name, description, icon, enabled, settings)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            description = VALUES(description),
            icon = VALUES(icon),
            enabled = VALUES(enabled),
            settings = VALUES(settings),
            updated_at = UTC_TIMESTAMP(6)
        """,
        (
            slug,
            name,
            description,
            icon,
            1 if enabled else 0,
            _serialise(settings),
        ),
    )
    return await get_module(slug)


async def update_module(
    slug: str,
    *,
    enabled: bool | None = None,
    settings: Any | None = None,
    name: str | None = None,
    description: str | None = None,
    icon: str | None = None,
) -> ModuleRecord | None:
    assignments: list[str] = []
    params: list[Any] = []
    if name is not None:
        assignments.append("name = %s")
        params.append(name)
    if description is not None:
        assignments.append("description = %s")
        params.append(description)
    if icon is not None:
        assignments.append("icon = %s")
        params.append(icon)
    if enabled is not None:
        assignments.append("enabled = %s")
        params.append(1 if enabled else 0)
    if settings is not None:
        assignments.append("settings = %s")
        params.append(_serialise(settings))
    if not assignments:
        return await get_module(slug)
    assignments.append("updated_at = UTC_TIMESTAMP(6)")
    params.append(slug)
    await db.execute(
        f"UPDATE integration_modules SET {', '.join(assignments)} WHERE slug = %s",
        tuple(params),
    )
    return await get_module(slug)
