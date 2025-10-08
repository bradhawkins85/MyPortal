from __future__ import annotations

from typing import Any, List, Optional

from app.core.database import db


def _normalise_task(row: dict[str, Any]) -> dict[str, Any]:
    task = dict(row)
    task["active"] = bool(int(task.get("active", 0)))
    if "company_id" in task and task["company_id"] is not None:
        task["company_id"] = int(task["company_id"])
    if "id" in task and task["id"] is not None:
        task["id"] = int(task["id"])
    return task


async def list_active_tasks() -> List[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM scheduled_tasks WHERE active = 1",
    )
    return [_normalise_task(row) for row in rows]


async def get_task(task_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        "SELECT * FROM scheduled_tasks WHERE id = %s",
        (task_id,),
    )
    return _normalise_task(row) if row else None


async def mark_task_run(task_id: int) -> None:
    await db.execute(
        "UPDATE scheduled_tasks SET last_run_at = UTC_TIMESTAMP() WHERE id = %s",
        (task_id,),
    )
