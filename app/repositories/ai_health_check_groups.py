"""Storage for the groups of computers that get monthly health checks (:mod:`app.services.ai_health_check_schedule`)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

_SELECT = (
    "SELECT g.id, g.name, g.company_id, g.target_rules_json, g.is_enabled, g.is_default, g.created_by_user_id, "
    "g.updated_by_user_id, g.created_at, g.updated_at, c.name AS company_name "
    "FROM ai_health_check_groups g LEFT JOIN companies c ON c.id = g.company_id "
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    group = dict(row)
    raw = group.pop("target_rules_json", None)
    try:
        rules = json.loads(raw) if raw else None
    except (TypeError, ValueError, RecursionError):
        rules = {}  # unreadable rules match no devices
    group["target_rules"] = rules if rules is None or isinstance(rules, dict) else {}
    group["is_enabled"] = bool(group.get("is_enabled"))
    group["is_default"] = bool(group.get("is_default"))
    return group


async def list_for_company(company_id: int | None) -> list[dict[str, Any]]:
    """Groups for every company, then the company's own (only the former without a company)."""

    if company_id is None:
        rows = await db.fetch_all(_SELECT + "WHERE g.company_id IS NULL ORDER BY g.name, g.id", ())
    else:
        rows = await db.fetch_all(
            _SELECT + "WHERE g.company_id IS NULL OR g.company_id = %s "
            "ORDER BY CASE WHEN g.company_id IS NULL THEN 0 ELSE 1 END, g.name, g.id",
            (int(company_id),),
        )
    return [_row(row) for row in rows or []]


async def list_enabled() -> list[dict[str, Any]]:
    rows = await db.fetch_all(_SELECT + "WHERE g.is_enabled = 1 ORDER BY g.id", ())
    return [_row(row) for row in rows or []]


async def get(group_id: int) -> dict[str, Any] | None:
    return _row(await db.fetch_one(_SELECT + "WHERE g.id = %s", (int(group_id),)))


async def create(*, name: str, company_id: int | None, target_rules: dict[str, Any] | None, is_enabled: bool,
                 user_id: int | None) -> int:
    now = _utcnow()
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO ai_health_check_groups (name, company_id, target_rules_json, is_enabled, created_by_user_id, "
        "updated_by_user_id, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (name[:255], company_id, json.dumps(target_rules) if target_rules is not None else None,
         1 if is_enabled else 0, user_id, user_id, now, now),
    ))


async def update(group_id: int, *, name: str, company_id: int | None, target_rules: dict[str, Any] | None,
                 is_enabled: bool, user_id: int | None) -> None:
    await db.execute(
        "UPDATE ai_health_check_groups SET name = %s, company_id = %s, target_rules_json = %s, is_enabled = %s, "
        "updated_by_user_id = %s, updated_at = %s WHERE id = %s",
        (name[:255], company_id, json.dumps(target_rules) if target_rules is not None else None,
         1 if is_enabled else 0, user_id, _utcnow(), int(group_id)),
    )


async def delete(group_id: int) -> None:
    await db.execute("DELETE FROM ai_health_check_groups WHERE id = %s", (int(group_id),))
