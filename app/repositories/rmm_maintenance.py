"""RMM maintenance windows and alert snoozes (:mod:`app.services.rmm_maintenance`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db

_SELECT = (
    "SELECT w.id, w.name, w.reason, w.company_id, w.asset_id, w.check_id, w.schedule_type, w.starts_at, "
    "w.ends_at, w.weekdays, w.start_time, w.duration_minutes, w.timezone, w.is_snooze, w.is_enabled, "
    "w.created_by_user_id, w.created_at, w.updated_at, "
    "c.name AS company_name, a.name AS asset_name, k.name AS check_name "
    "FROM rmm_maintenance_windows w "
    "LEFT JOIN companies c ON c.id = w.company_id "
    "LEFT JOIN assets a ON a.id = w.asset_id "
    "LEFT JOIN rmm_checks k ON k.id = w.check_id"
)

_COLUMNS = (
    "name", "reason", "company_id", "asset_id", "check_id", "schedule_type", "starts_at", "ends_at",
    "weekdays", "start_time", "duration_minutes", "timezone", "is_snooze", "is_enabled",
)


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    record = dict(row)
    for key in ("id", "company_id", "asset_id", "check_id", "duration_minutes", "created_by_user_id"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    record["is_snooze"] = bool(record.get("is_snooze"))
    record["is_enabled"] = bool(record.get("is_enabled"))
    return record


def _rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [record for record in (_normalise(row) for row in rows or []) if record]


async def list_enabled() -> list[dict[str, Any]]:
    """Every enabled window; the list is small and cached by the service."""

    return _rows(await db.fetch_all(_SELECT + " WHERE w.is_enabled = 1 ORDER BY w.id"))


async def list_for_company(company_id: int) -> list[dict[str, Any]]:
    """Windows reaching a company: its own, its devices' and those for every company."""

    return _rows(await db.fetch_all(
        _SELECT + " WHERE w.company_id IS NULL OR w.company_id = %s ORDER BY w.is_enabled DESC, w.id DESC",
        (company_id,),
    ))


async def get(window_id: int) -> dict[str, Any] | None:
    return _normalise(await db.fetch_one(_SELECT + " WHERE w.id = %s", (window_id,)))


async def create(values: dict[str, Any], *, created_by_user_id: int | None) -> int:
    params = tuple(values.get(column) for column in _COLUMNS) + (created_by_user_id,)
    window_id = await db.execute_returning_lastrowid(
        "INSERT INTO rmm_maintenance_windows (name, reason, company_id, asset_id, check_id, schedule_type, "
        "starts_at, ends_at, weekdays, start_time, duration_minutes, timezone, is_snooze, is_enabled, "
        "created_by_user_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        params,
    )
    return int(window_id)


async def update(window_id: int, values: dict[str, Any]) -> None:
    params = tuple(values.get(column) for column in _COLUMNS) + (window_id,)
    await db.execute(
        "UPDATE rmm_maintenance_windows SET name = %s, reason = %s, company_id = %s, asset_id = %s, "
        "check_id = %s, schedule_type = %s, starts_at = %s, ends_at = %s, weekdays = %s, start_time = %s, "
        "duration_minutes = %s, timezone = %s, is_snooze = %s, is_enabled = %s WHERE id = %s",
        params,
    )


async def end_now(window_id: int, now: datetime) -> None:
    """End a one-off window now (it stays listed as ended) or switch a weekly one off."""

    await db.execute(
        "UPDATE rmm_maintenance_windows SET ends_at = CASE WHEN schedule_type = 'once' THEN %s ELSE ends_at END, "
        "is_enabled = CASE WHEN schedule_type = 'once' THEN is_enabled ELSE 0 END WHERE id = %s",
        (now, window_id),
    )


async def delete(window_id: int) -> None:
    await db.execute("DELETE FROM rmm_maintenance_windows WHERE id = %s", (window_id,))


async def delete_ended_snoozes(before: datetime) -> int:
    return await db.execute_rowcount(
        "DELETE FROM rmm_maintenance_windows WHERE is_snooze = 1 AND schedule_type = 'once' AND ends_at < %s",
        (before,),
    )
