"""Storage for monthly health checks (:mod:`app.services.ai_health_check_schedule`)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db

_COLUMNS = (
    "asset_id, company_id, next_run_at, window_ends_at, last_started_at, last_health_check_id, last_result, "
    "created_at, updated_at"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _clip(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


async def list_unscheduled_agents() -> list[dict[str, Any]]:
    """The latest active RMM agent of each asset in its own company that has no schedule yet."""

    rows = await db.fetch_all(
        "SELECT g.id, g.asset_id, g.company_id, g.os, g.last_seen_utc FROM rmm_agents g "
        "INNER JOIN assets a ON a.id = g.asset_id "
        "WHERE g.status = 'active' AND g.company_id = a.company_id AND a.archived_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM ai_health_check_schedules s WHERE s.asset_id = a.id) "
        "ORDER BY g.asset_id, g.last_seen_utc DESC, g.id DESC",
        (),
    )
    agents: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        agents.setdefault(int(row["asset_id"]), dict(row))
    return list(agents.values())


async def list_scheduled_agents() -> list[dict[str, Any]]:
    """The latest active RMM agent of each asset that has a schedule."""

    rows = await db.fetch_all(
        "SELECT g.id, g.asset_id, g.company_id, g.os, g.last_seen_utc FROM rmm_agents g "
        "INNER JOIN ai_health_check_schedules s ON s.asset_id = g.asset_id "
        "WHERE g.status = 'active' AND g.company_id = s.company_id "
        "ORDER BY g.asset_id, g.last_seen_utc DESC, g.id DESC",
        (),
    )
    agents: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        agents.setdefault(int(row["asset_id"]), dict(row))
    return list(agents.values())


async def list_for_company(company_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    """A company's monthly health checks, soonest first."""

    rows = await db.fetch_all(
        "SELECT s.asset_id, s.company_id, s.next_run_at, s.window_ends_at, s.last_started_at, "
        "s.last_health_check_id, s.last_result, a.name AS asset_name FROM ai_health_check_schedules s "
        "LEFT JOIN assets a ON a.id = s.asset_id WHERE s.company_id = %s ORDER BY s.next_run_at, s.asset_id LIMIT %s",
        (int(company_id), int(limit)),
    )
    return [dict(row) for row in rows or []]


async def count_for_company(company_id: int) -> int:
    row = await db.fetch_one(
        "SELECT COUNT(*) AS total FROM ai_health_check_schedules WHERE company_id = %s", (int(company_id),)
    )
    return int((row or {}).get("total") or 0)


async def create(*, asset_id: int, company_id: int, next_run_at: datetime, window_ends_at: datetime) -> None:
    now = _utcnow()
    await db.execute(
        "INSERT INTO ai_health_check_schedules (asset_id, company_id, next_run_at, window_ends_at, created_at, "
        "updated_at) SELECT %s, %s, %s, %s, %s, %s WHERE NOT EXISTS "
        "(SELECT 1 FROM ai_health_check_schedules WHERE asset_id = %s)",
        (int(asset_id), int(company_id), next_run_at, window_ends_at, now, now, int(asset_id)),
    )


async def get(asset_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT " + _COLUMNS + " FROM ai_health_check_schedules WHERE asset_id = %s",  # nosec B608 - fixed columns
        (int(asset_id),),
    )
    return dict(row) if row else None


async def list_due(now: datetime, *, limit: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT " + _COLUMNS + " FROM ai_health_check_schedules "  # nosec B608 - fixed columns
        "WHERE next_run_at <= %s ORDER BY next_run_at, asset_id LIMIT %s",
        (now, int(limit)),
    )
    return [dict(row) for row in rows or []]


async def postpone(asset_id: int, *, next_run_at: datetime, result: str) -> None:
    """Try again later in the same month."""

    await db.execute(
        "UPDATE ai_health_check_schedules SET next_run_at = %s, last_result = %s, updated_at = %s "
        "WHERE asset_id = %s",
        (next_run_at, _clip(result, 512), _utcnow(), int(asset_id)),
    )


async def reschedule(
    asset_id: int,
    *,
    next_run_at: datetime,
    window_ends_at: datetime,
    result: str,
    started_at: datetime | None = None,
    health_check_id: int | None = None,
) -> None:
    """Move on to the next month, recording what happened in this one."""

    assignments = ["next_run_at = %s", "window_ends_at = %s", "last_result = %s", "updated_at = %s"]
    params: list[Any] = [next_run_at, window_ends_at, _clip(result, 512), _utcnow()]
    if started_at is not None:
        assignments.append("last_started_at = %s")
        params.append(started_at)
    if health_check_id is not None:
        assignments.append("last_health_check_id = %s")
        params.append(int(health_check_id))
    params.append(int(asset_id))
    await db.execute(
        "UPDATE ai_health_check_schedules SET " + ", ".join(assignments)  # nosec B608 - fixed columns
        + " WHERE asset_id = %s",
        tuple(params),
    )


async def delete(asset_id: int) -> None:
    await db.execute("DELETE FROM ai_health_check_schedules WHERE asset_id = %s", (int(asset_id),))
