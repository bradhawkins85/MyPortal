from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db

TaskRecord = dict[str, Any]


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _normalise_task(row: dict[str, Any]) -> TaskRecord:
    record = dict(row)
    for key in ("id", "ticket_id", "completed_by", "sort_order"):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    for key in ("created_at", "updated_at", "completed_at"):
        record[key] = _make_aware(record.get(key))
    if "is_completed" in record:
        record["is_completed"] = bool(record.get("is_completed"))
    return record


async def create_task(
    *,
    ticket_id: int,
    task_name: str,
    sort_order: int = 0,
) -> TaskRecord:
    task_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO ticket_tasks (ticket_id, task_name, sort_order)
        VALUES (%s, %s, %s)
        """,
        (ticket_id, task_name, sort_order),
    )
    if task_id:
        row = await db.fetch_one("SELECT * FROM ticket_tasks WHERE id = %s", (task_id,))
        if row:
            return _normalise_task(row)
    fallback_row: dict[str, Any] = {
        "id": task_id,
        "ticket_id": ticket_id,
        "task_name": task_name,
        "is_completed": False,
        "completed_at": None,
        "completed_by": None,
        "sort_order": sort_order,
        "created_at": None,
        "updated_at": None,
    }
    return _normalise_task(fallback_row)


async def list_tasks(ticket_id: int) -> list[TaskRecord]:
    rows = await db.fetch_all(
        """
        SELECT * FROM ticket_tasks
        WHERE ticket_id = %s
        ORDER BY sort_order ASC, id ASC
        """,
        (ticket_id,),
    )
    return [_normalise_task(row) for row in rows]


async def get_task(task_id: int) -> TaskRecord | None:
    row = await db.fetch_one("SELECT * FROM ticket_tasks WHERE id = %s", (task_id,))
    return _normalise_task(row) if row else None


async def update_task(
    task_id: int,
    *,
    task_name: str | None = None,
    is_completed: bool | None = None,
    completed_by: int | None = None,
    sort_order: int | None = None,
) -> TaskRecord | None:
    assignments: list[str] = []
    params: list[Any] = []
    
    if task_name is not None:
        assignments.append("task_name = %s")
        params.append(task_name)
    
    if is_completed is not None:
        assignments.append("is_completed = %s")
        params.append(1 if is_completed else 0)
        if is_completed:
            assignments.append("completed_at = UTC_TIMESTAMP(6)")
            if completed_by is not None:
                assignments.append("completed_by = %s")
                params.append(completed_by)
        else:
            assignments.append("completed_at = NULL")
            assignments.append("completed_by = NULL")
    
    if sort_order is not None:
        assignments.append("sort_order = %s")
        params.append(sort_order)
    
    if not assignments:
        return await get_task(task_id)
    
    query = f"UPDATE ticket_tasks SET {', '.join(assignments)} WHERE id = %s"
    params.append(task_id)
    await db.execute(query, tuple(params))
    return await get_task(task_id)


async def delete_task(task_id: int) -> None:
    await db.execute("DELETE FROM ticket_tasks WHERE id = %s", (task_id,))
