"""Storage for health check remediation scripts (:mod:`app.services.health_check_remediation`)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

_SELECT = (
    "SELECT r.id, r.detection_key, r.company_id, r.script_id, r.entries_encrypted, r.inputs_json, "
    "r.timeout_seconds, r.is_enabled, r.create_ticket, r.run_count, r.last_run_at, r.last_summary, "
    "r.created_by_user_id, r.updated_by_user_id, r.created_at, r.updated_at, s.name AS script_name, s.path AS script_path, "
    "s.is_active AS script_active, s.company_id AS script_company_id, c.name AS company_name "
    "FROM ai_health_check_remediations r "
    "LEFT JOIN rmm_scripts s ON s.id = r.script_id "
    "LEFT JOIN companies c ON c.id = r.company_id "
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row(row: Any, *, with_secrets: bool = False) -> dict[str, Any] | None:
    if not row:
        return None
    link = dict(row)
    try:
        inputs = json.loads(link.pop("inputs_json", None) or "[]")
    except (TypeError, ValueError):
        inputs = []
    link["inputs"] = inputs if isinstance(inputs, list) else []
    link["is_enabled"] = bool(link.get("is_enabled"))
    link["create_ticket"] = bool(link.get("create_ticket"))
    link["script_active"] = bool(link.get("script_active"))
    if not with_secrets:
        link.pop("entries_encrypted", None)
    return link


async def list_for_company(company_id: int | None, *, with_secrets: bool = False) -> list[dict[str, Any]]:
    """Links for every company, then the company's own (only the former without a company)."""

    if company_id is None:
        rows = await db.fetch_all(_SELECT + "WHERE r.company_id IS NULL ORDER BY r.detection_key", ())
    else:
        rows = await db.fetch_all(
            _SELECT + "WHERE r.company_id IS NULL OR r.company_id = %s "
            "ORDER BY r.detection_key, CASE WHEN r.company_id IS NULL THEN 0 ELSE 1 END",
            (int(company_id),),
        )
    return [_row(row, with_secrets=with_secrets) for row in rows or []]


async def get(link_id: int, *, with_secrets: bool = False) -> dict[str, Any] | None:
    return _row(await db.fetch_one(_SELECT + "WHERE r.id = %s", (int(link_id),)), with_secrets=with_secrets)


async def get_for_scope(detection_key: str, company_id: int | None, *, with_secrets: bool = False) -> dict[str, Any] | None:
    return _row(await db.fetch_one(
        _SELECT + "WHERE r.detection_key = %s AND r.scope_company_id = %s",
        (str(detection_key), int(company_id or 0)),
    ), with_secrets=with_secrets)


async def save(
    *,
    detection_key: str,
    company_id: int | None,
    script_id: int | None,
    entries_encrypted: str | None,
    inputs: list[dict[str, Any]],
    timeout_seconds: int,
    is_enabled: bool,
    user_id: int | None,
    create_ticket: bool = False,
) -> int:
    """Create or replace the detection's link for the scope (``None`` is every company)."""

    now = _utcnow()
    existing = await get_for_scope(detection_key, company_id)
    values = (script_id, entries_encrypted, json.dumps(inputs), int(timeout_seconds), 1 if is_enabled else 0,
              1 if create_ticket else 0)
    if existing:
        await db.execute(
            "UPDATE ai_health_check_remediations SET script_id = %s, entries_encrypted = %s, inputs_json = %s, "
            "timeout_seconds = %s, is_enabled = %s, create_ticket = %s, updated_by_user_id = %s, updated_at = %s "
            "WHERE id = %s",
            values + (user_id, now, int(existing["id"])),
        )
        return int(existing["id"])
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO ai_health_check_remediations (detection_key, company_id, scope_company_id, script_id, "
        "entries_encrypted, inputs_json, timeout_seconds, is_enabled, create_ticket, created_by_user_id, "
        "updated_by_user_id, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (str(detection_key), company_id, int(company_id or 0)) + values + (user_id, user_id, now, now),
    ))


async def delete(link_id: int) -> None:
    await db.execute("DELETE FROM ai_health_check_remediations WHERE id = %s", (int(link_id),))


async def record_run(link_id: int, summary: str) -> None:
    await db.execute(
        "UPDATE ai_health_check_remediations SET run_count = run_count + 1, last_run_at = %s, last_summary = %s "
        "WHERE id = %s",
        (_utcnow(), summary[:512], int(link_id)),
    )


async def get_ticket(detection_key: str, asset_id: int) -> int | None:
    """The ticket a detection last raised for an asset."""

    row = await db.fetch_one(
        "SELECT ticket_id FROM ai_health_check_detection_tickets WHERE detection_key = %s AND asset_id = %s",
        (str(detection_key), int(asset_id)),
    )
    return int(row["ticket_id"]) if row and row.get("ticket_id") is not None else None


async def set_ticket(detection_key: str, asset_id: int, ticket_id: int) -> None:
    now = _utcnow()
    if await get_ticket(detection_key, asset_id) is not None:
        await db.execute(
            "UPDATE ai_health_check_detection_tickets SET ticket_id = %s, updated_at = %s "
            "WHERE detection_key = %s AND asset_id = %s",
            (int(ticket_id), now, str(detection_key), int(asset_id)),
        )
        return
    await db.execute(
        "INSERT INTO ai_health_check_detection_tickets (detection_key, asset_id, ticket_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (str(detection_key), int(asset_id), int(ticket_id), now, now),
    )
