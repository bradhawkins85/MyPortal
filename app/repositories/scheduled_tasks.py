from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from app.core.database import db


def _make_aware(dt: Any) -> datetime | None:
    if not dt:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _normalise_task(row: dict[str, Any]) -> dict[str, Any]:
    task = dict(row)
    task["active"] = bool(int(task.get("active", 0)))
    for key in ("company_id", "id", "max_retries", "retry_backoff_seconds"):
        if key in task and task[key] is not None:
            task[key] = int(task[key])
    if "last_run_at" in task and task["last_run_at"]:
        task["last_run_at"] = _make_aware(task["last_run_at"])
    return task


def _normalise_run(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    if "id" in data and data["id"] is not None:
        data["id"] = int(data["id"])
    if "task_id" in data and data["task_id"] is not None:
        data["task_id"] = int(data["task_id"])
    if data.get("started_at"):
        data["started_at"] = _make_aware(data["started_at"])
    if data.get("finished_at"):
        data["finished_at"] = _make_aware(data["finished_at"])
    if "duration_ms" in data and data["duration_ms"] is not None:
        data["duration_ms"] = int(data["duration_ms"])
    return data


async def list_tasks(include_inactive: bool = True) -> list[dict[str, Any]]:
    where = "" if include_inactive else "WHERE active = 1"
    rows = await db.fetch_all(
        f"SELECT * FROM scheduled_tasks {where} ORDER BY name ASC",
    )
    return [_normalise_task(row) for row in rows]


async def list_active_tasks() -> list[dict[str, Any]]:
    return await list_tasks(include_inactive=False)


async def get_task(task_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM scheduled_tasks WHERE id = %s",
        (task_id,),
    )
    return _normalise_task(row) if row else None


async def create_task(
    *,
    name: str,
    command: str,
    cron: str,
    company_id: int | None = None,
    description: str | None = None,
    active: bool = True,
    max_retries: int = 12,
    retry_backoff_seconds: int = 300,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO scheduled_tasks
            (company_id, name, command, cron, description, active, max_retries, retry_backoff_seconds)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            company_id,
            name,
            command,
            cron,
            description,
            1 if active else 0,
            max(0, max_retries),
            max(1, retry_backoff_seconds),
        ),
    )
    created = await db.fetch_one(
        "SELECT * FROM scheduled_tasks WHERE id = LAST_INSERT_ID()",
    )
    return _normalise_task(created) if created else {}


async def update_task(
    task_id: int,
    *,
    name: str,
    command: str,
    cron: str,
    company_id: int | None,
    description: str | None,
    active: bool,
    max_retries: int,
    retry_backoff_seconds: int,
) -> dict[str, Any]:
    await db.execute(
        """
        UPDATE scheduled_tasks
        SET company_id = %s,
            name = %s,
            command = %s,
            cron = %s,
            description = %s,
            active = %s,
            max_retries = %s,
            retry_backoff_seconds = %s
        WHERE id = %s
        """,
        (
            company_id,
            name,
            command,
            cron,
            description,
            1 if active else 0,
            max(0, max_retries),
            max(1, retry_backoff_seconds),
            task_id,
        ),
    )
    updated = await get_task(task_id)
    return updated or {}


async def delete_task(task_id: int) -> None:
    await db.execute("DELETE FROM scheduled_tasks WHERE id = %s", (task_id,))


async def set_task_active(task_id: int, active: bool) -> dict[str, Any] | None:
    await db.execute(
        "UPDATE scheduled_tasks SET active = %s WHERE id = %s",
        (1 if active else 0, task_id),
    )
    return await get_task(task_id)


async def record_task_run(
    task_id: int,
    *,
    status: str,
    started_at: datetime,
    finished_at: datetime | None,
    duration_ms: int | None,
    details: str | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO scheduled_task_runs (task_id, status, started_at, finished_at, duration_ms, details)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            task_id,
            status,
            started_at.replace(tzinfo=None),
            finished_at.replace(tzinfo=None) if finished_at else None,
            duration_ms,
            details,
        ),
    )
    await db.execute(
        """
        UPDATE scheduled_tasks
        SET last_run_at = %s,
            last_status = %s,
            last_error = %s
        WHERE id = %s
        """,
        (
            finished_at.replace(tzinfo=None) if finished_at else started_at.replace(tzinfo=None),
            status,
            details,
            task_id,
        ),
    )


async def list_recent_runs(task_ids: Sequence[int] | None = None, limit: int = 50) -> list[dict[str, Any]]:
    if task_ids:
        placeholders = ",".join(["%s"] * len(task_ids))
        rows = await db.fetch_all(
            f"""
            SELECT r.*, t.name AS task_name
            FROM scheduled_task_runs AS r
            JOIN scheduled_tasks AS t ON t.id = r.task_id
            WHERE r.task_id IN ({placeholders})
            ORDER BY r.started_at DESC
            LIMIT %s
            """,
            tuple(task_ids) + (limit,),
        )
    else:
        rows = await db.fetch_all(
            """
            SELECT r.*, t.name AS task_name
            FROM scheduled_task_runs AS r
            JOIN scheduled_tasks AS t ON t.id = r.task_id
            ORDER BY r.started_at DESC
            LIMIT %s
            """,
            (limit,),
        )
    return [_normalise_run(row) for row in rows]


async def mark_task_run(task_id: int) -> None:
    now = datetime.utcnow()
    await db.execute(
        "UPDATE scheduled_tasks SET last_run_at = %s WHERE id = %s",
        (now, task_id),
    )
