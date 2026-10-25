"""Storage for AI health checks (:mod:`app.services.ai_health_check`)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.database import db

_COLUMNS = (
    "h.id, h.company_id, h.asset_id, h.tray_device_id, h.ticket_id, h.command_id, h.status, h.stage_message, "
    "h.platform, h.model, h.allow_pawnio, h.pawnio_status, h.overall_status, h.health_score, h.summary, "
    "h.snapshot_json, h.report_json, h.logs_json, h.log_requests_json, h.error, h.requested_by_user_id, "
    "h.created_at, h.updated_at, h.completed_at"
)
_LIST_COLUMNS = (
    "h.id, h.company_id, h.asset_id, h.tray_device_id, h.ticket_id, h.command_id, h.status, h.stage_message, "
    "h.platform, h.model, h.allow_pawnio, h.pawnio_status, h.overall_status, h.health_score, h.summary, "
    "h.error, h.requested_by_user_id, h.created_at, h.updated_at, h.completed_at"
)
_JOINS = (
    " FROM ai_health_checks h LEFT JOIN assets a ON a.id = h.asset_id "
    "LEFT JOIN companies c ON c.id = h.company_id"
)
_NAMES = ", a.name AS asset_name, c.name AS company_name"

ACTIVE_STATUSES = ("queued", "collecting", "analysing")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _decode(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return default
    return decoded if isinstance(decoded, type(default)) else default


def _row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    record = dict(row)
    for key, default in (
        ("snapshot_json", {}), ("report_json", {}), ("logs_json", {}), ("log_requests_json", []),
    ):
        if key in record:
            record[key.removesuffix("_json")] = _decode(record.pop(key), default)
    record["allow_pawnio"] = bool(record.get("allow_pawnio"))
    return record


def _clip(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


async def create(
    *,
    company_id: int,
    asset_id: int,
    tray_device_id: int | None,
    ticket_id: int | None,
    platform: str,
    model: str,
    allow_pawnio: bool,
    requested_by_user_id: int | None,
) -> int:
    now = _utcnow()
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO ai_health_checks (company_id, asset_id, tray_device_id, ticket_id, status, platform, model, "
        "allow_pawnio, requested_by_user_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s, %s, %s, %s)",
        (int(company_id), int(asset_id), tray_device_id, ticket_id, platform[:16], model[:255],
         1 if allow_pawnio else 0, requested_by_user_id, now, now),
    ))


async def get(health_check_id: int) -> dict[str, Any] | None:
    return _row(await db.fetch_one(
        "SELECT " + _COLUMNS + _NAMES + _JOINS + " WHERE h.id = %s",  # nosec B608 - fixed column list
        (int(health_check_id),),
    ))


async def list_checks(
    *, asset_id: int | None = None, company_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if asset_id is not None:
        where.append("h.asset_id = %s")
        params.append(int(asset_id))
    if company_id is not None:
        where.append("h.company_id = %s")
        params.append(int(company_id))
    sql = "SELECT " + _LIST_COLUMNS + _NAMES + _JOINS  # nosec B608 - fixed column list
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY h.created_at DESC, h.id DESC LIMIT %s"
    params.append(int(limit))
    rows = await db.fetch_all(sql, tuple(params))
    return [_row(row) for row in rows or []]


async def get_active_for_asset(asset_id: int) -> dict[str, Any] | None:
    return _row(await db.fetch_one(
        "SELECT " + _LIST_COLUMNS + _NAMES + _JOINS  # nosec B608 - fixed column list
        + " WHERE h.asset_id = %s AND h.status IN ('queued', 'collecting', 'analysing') "
        "ORDER BY h.created_at DESC, h.id DESC LIMIT 1",
        (int(asset_id),),
    ))


async def update(health_check_id: int, **fields: Any) -> None:
    """Update the given columns. JSON fields are passed as Python values."""

    allowed = {
        "status": 24, "stage_message": 1024, "pawnio_status": 512, "overall_status": 16, "error": 1024,
        "summary": 8000, "model": 255,
    }
    assignments: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key in allowed:
            assignments.append(key + " = %s")
            params.append(_clip(value, allowed[key]))
        elif key in {"snapshot", "report", "logs", "log_requests"}:
            assignments.append(key + "_json = %s")
            params.append(json.dumps(value, default=str) if value is not None else None)
        elif key in {"command_id", "health_score"}:
            assignments.append(key + " = %s")
            params.append(int(value) if value is not None else None)
        elif key == "completed":
            assignments.append("completed_at = %s")
            params.append(_utcnow() if value else None)
        else:
            raise ValueError(f"Unknown health check field: {key}")
    if not assignments:
        return
    assignments.append("updated_at = %s")
    params.append(_utcnow())
    params.append(int(health_check_id))
    await db.execute(
        "UPDATE ai_health_checks SET " + ", ".join(assignments) + " WHERE id = %s",  # nosec B608 - fixed columns
        tuple(params),
    )


async def list_stale(*, queued_hours: int, running_hours: int) -> list[dict[str, Any]]:
    """Checks the device or the server never finished.

    A check waiting for its first snapshot gets ``queued_hours`` (the device
    may be offline); anything else gets ``running_hours``.
    """

    now = _utcnow()
    rows = await db.fetch_all(
        "SELECT id, command_id, status, CASE WHEN snapshot_json IS NULL THEN 0 ELSE 1 END AS has_snapshot "
        "FROM ai_health_checks WHERE "
        "(status = 'queued' AND snapshot_json IS NULL AND updated_at < %s) OR "
        "(status IN ('queued', 'collecting', 'analysing') AND (status <> 'queued' OR snapshot_json IS NOT NULL) "
        "AND updated_at < %s)",
        (now - timedelta(hours=queued_hours), now - timedelta(hours=running_hours)),
    )
    return [dict(row) for row in rows or []]


_DEVICE_COLUMNS = (
    "td.id, td.company_id, td.asset_id, td.device_uid, td.os, td.os_version, td.hostname, td.agent_version, "
    "td.last_seen_utc, td.status"
)


async def find_tray_device_for_asset(asset_id: int) -> dict[str, Any] | None:
    """The asset's active tray device in its own company, as tickets resolve it.

    A device linked to the asset wins; otherwise an unlinked device with the
    asset's serial number or name.
    """

    row = await db.fetch_one(
        "SELECT " + _DEVICE_COLUMNS + " FROM tray_devices AS td "  # nosec B608 - fixed column list
        "INNER JOIN assets AS a ON a.id = %s "
        "WHERE td.status = 'active' AND td.company_id = a.company_id AND ("
        " td.asset_id = a.id OR (a.tray_device_uid IS NOT NULL AND td.device_uid = a.tray_device_uid) OR ("
        "  (td.asset_id IS NULL OR td.asset_id = 0) AND ("
        "   (a.serial_number IS NOT NULL AND a.serial_number <> '' AND LOWER(td.serial_number) = LOWER(a.serial_number))"
        "   OR (a.name IS NOT NULL AND a.name <> '' AND LOWER(td.hostname) = LOWER(a.name))))) "
        "ORDER BY CASE WHEN td.asset_id = a.id THEN 0 ELSE 1 END, td.last_seen_utc DESC, td.id DESC LIMIT 1",
        (int(asset_id),),
    )
    return dict(row) if row else None
