from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db


async def list_templates(company_id: int) -> list[dict[str, Any]]:
    return list(await db.fetch_all(
        "SELECT * FROM process_templates WHERE company_id = %s ORDER BY name, id",
        (company_id,),
    ) or [])


async def get_template(company_id: int, template_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM process_templates WHERE id = %s AND company_id = %s",
        (template_id, company_id),
    )


async def template_steps(template_id: int, version: int) -> list[dict[str, Any]]:
    return list(await db.fetch_all(
        "SELECT step_order, title, instructions FROM process_template_steps WHERE template_id = %s AND version_number = %s ORDER BY step_order",
        (template_id, version),
    ) or [])


async def create_template(company_id: int, name: str, description: str | None,
                          steps: list[dict[str, str | None]], user_id: int) -> int:
    template_id = await db.execute_returning_lastrowid(
        "INSERT INTO process_templates (company_id, name, description, created_by) VALUES (%s, %s, %s, %s)",
        (company_id, name, description, user_id),
    )
    await db.execute_many(
        "INSERT INTO process_template_steps (template_id, version_number, step_order, title, instructions) VALUES (%s, %s, %s, %s, %s)",
        [(template_id, 1, index, step["title"], step.get("instructions")) for index, step in enumerate(steps, 1)],
    )
    return template_id


async def add_version(template_id: int, current_version: int,
                      name: str, description: str | None,
                      steps: list[dict[str, str | None]]) -> int:
    version = current_version + 1
    await db.execute_many(
        "INSERT INTO process_template_steps (template_id, version_number, step_order, title, instructions) VALUES (%s, %s, %s, %s, %s)",
        [(template_id, version, index, step["title"], step.get("instructions")) for index, step in enumerate(steps, 1)],
    )
    await db.execute(
        "UPDATE process_templates SET name = %s, description = %s, current_version = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s AND current_version = %s",
        (name, description, version, template_id, current_version),
    )
    return version


async def company_reference_exists(table: str, company_id: int, record_id: int) -> bool:
    if table not in {"assets", "tickets"}:
        raise ValueError("Unsupported reference table")
    row = await db.fetch_one(
        "SELECT id FROM " + table + " WHERE id = %s AND company_id = %s",
        (record_id, company_id),
    )
    return bool(row)


async def start_run(*, company_id: int, template: dict[str, Any], asset_id: int | None,
                    ticket_id: int | None, assignee_id: int | None, priority: str,
                    due_at: datetime | None, user_id: int) -> int:
    version = int(template["current_version"])
    steps = await template_steps(int(template["id"]), version)
    run_id = await db.execute_returning_lastrowid(
        "INSERT INTO process_runs (company_id, template_id, template_version, template_name, asset_id, ticket_id, assignee_id, priority, due_at, started_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (company_id, template["id"], version, template["name"], asset_id, ticket_id, assignee_id, priority, due_at, user_id),
    )
    await db.execute_many(
        "INSERT INTO process_run_steps (run_id, step_order, title, instructions) VALUES (%s, %s, %s, %s)",
        [(run_id, step["step_order"], step["title"], step.get("instructions")) for step in steps],
    )
    return run_id


async def get_run(company_id: int, run_id: int) -> dict[str, Any] | None:
    run = await db.fetch_one("SELECT * FROM process_runs WHERE id = %s AND company_id = %s", (run_id, company_id))
    if run:
        run["steps"] = list(await db.fetch_all("SELECT * FROM process_run_steps WHERE run_id = %s ORDER BY step_order", (run_id,)) or [])
    return run


async def list_runs(company_id: int) -> list[dict[str, Any]]:
    return list(await db.fetch_all("SELECT * FROM process_runs WHERE company_id = %s ORDER BY started_at DESC, id DESC", (company_id,)) or [])


async def update_step(company_id: int, run_id: int, step_id: int, *, status: str,
                      notes: str | None, user_id: int) -> bool:
    completed = status == "completed"
    count = await db.execute_rowcount(
        "UPDATE process_run_steps SET status = %s, notes = %s, completed_by = %s, completed_at = %s WHERE id = %s AND run_id = %s AND EXISTS (SELECT 1 FROM process_runs WHERE id = %s AND company_id = %s)",
        (status, notes, user_id if completed else None, datetime.utcnow() if completed else None, step_id, run_id, run_id, company_id),
    )
    return count == 1


async def update_run_status(company_id: int, run_id: int, status: str, user_id: int) -> bool:
    completed = status == "completed"
    count = await db.execute_rowcount(
        "UPDATE process_runs SET status = %s, completed_by = %s, completed_at = %s WHERE id = %s AND company_id = %s",
        (status, user_id if completed else None, datetime.utcnow() if completed else None, run_id, company_id),
    )
    return count == 1
