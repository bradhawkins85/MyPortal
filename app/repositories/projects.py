"""Projects, phases, task tickets and milestones (:mod:`app.services.projects`)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from app.core.database import db

_PROJECT_SELECT = (
    "SELECT p.id, p.company_id, p.name, p.description, p.status, p.billing_type, p.budget_hours, p.budget_amount, "
    "p.start_date, p.due_date, p.manager_user_id, p.created_by_user_id, p.created_at, p.updated_at, "
    "c.name AS company_name, u.first_name AS manager_first_name, u.last_name AS manager_last_name, "
    "u.email AS manager_email FROM projects p LEFT JOIN companies c ON c.id = p.company_id "
    "LEFT JOIN users u ON u.id = p.manager_user_id"
)
_PROJECT_COLUMNS = ("company_id", "name", "description", "status", "billing_type", "budget_hours", "budget_amount",
                    "start_date", "due_date", "manager_user_id")


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _project(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    record = dict(row)
    for key in ("id", "company_id", "manager_user_id", "created_by_user_id"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    for key in ("budget_hours", "budget_amount"):
        if record.get(key) is not None:
            record[key] = float(record[key])
    record["start_date"] = _as_date(record.get("start_date"))
    record["due_date"] = _as_date(record.get("due_date"))
    return record


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #


async def list_projects(*, company_id: int | None = None) -> list[dict[str, Any]]:
    if company_id is not None:
        rows = await db.fetch_all(_PROJECT_SELECT + " WHERE p.company_id = %s ORDER BY p.due_date IS NULL, p.due_date, p.id",
                                  (company_id,))
    else:
        rows = await db.fetch_all(_PROJECT_SELECT + " ORDER BY p.due_date IS NULL, p.due_date, p.id")
    return [record for record in (_project(row) for row in rows or []) if record]


async def get_project(project_id: int) -> dict[str, Any] | None:
    return _project(await db.fetch_one(_PROJECT_SELECT + " WHERE p.id = %s", (project_id,)))


async def create_project(values: dict[str, Any], *, user_id: int | None) -> int:
    params = tuple(values.get(column) for column in _PROJECT_COLUMNS) + (user_id,)
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO projects (company_id, name, description, status, billing_type, budget_hours, budget_amount, "
        "start_date, due_date, manager_user_id, created_by_user_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        params,
    ))


async def update_project(project_id: int, values: dict[str, Any]) -> None:
    params = tuple(values.get(column) for column in _PROJECT_COLUMNS) + (project_id,)
    await db.execute(
        "UPDATE projects SET company_id = %s, name = %s, description = %s, status = %s, billing_type = %s, "
        "budget_hours = %s, budget_amount = %s, start_date = %s, due_date = %s, manager_user_id = %s WHERE id = %s",
        params,
    )


async def delete_project(project_id: int) -> None:
    await db.execute("DELETE FROM projects WHERE id = %s", (project_id,))


# --------------------------------------------------------------------------- #
# Phases
# --------------------------------------------------------------------------- #


async def list_phases(project_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, project_id, name, position, due_date FROM project_phases WHERE project_id = %s "
        "ORDER BY position, id",
        (project_id,),
    )
    return [{**dict(row), "id": int(row["id"]), "due_date": _as_date(row.get("due_date"))} for row in rows or []]


async def get_phase(phase_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT id, project_id, name, position, due_date FROM project_phases WHERE id = %s",
                             (phase_id,))
    return {**dict(row), "id": int(row["id"]), "project_id": int(row["project_id"])} if row else None


async def create_phase(project_id: int, name: str, due_date: date | None) -> int:
    row = await db.fetch_one("SELECT MAX(position) AS last FROM project_phases WHERE project_id = %s", (project_id,))
    position = int((row or {}).get("last") or 0) + 1
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO project_phases (project_id, name, position, due_date) VALUES (%s, %s, %s, %s)",
        (project_id, name, position, due_date),
    ))


async def update_phase(phase_id: int, name: str, due_date: date | None) -> None:
    await db.execute("UPDATE project_phases SET name = %s, due_date = %s WHERE id = %s", (name, due_date, phase_id))


async def move_phase(phase_id: int, position: int) -> None:
    await db.execute("UPDATE project_phases SET position = %s WHERE id = %s", (position, phase_id))


async def delete_phase(phase_id: int) -> None:
    await db.execute("DELETE FROM project_phases WHERE id = %s", (phase_id,))


# --------------------------------------------------------------------------- #
# Task tickets
# --------------------------------------------------------------------------- #


async def list_tasks(project_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT pt.ticket_id, pt.phase_id, pt.estimate_hours, pt.position, t.ticket_number, t.subject, t.status, "
        "t.priority, t.assigned_user_id, u.first_name AS assignee_first_name, u.last_name AS assignee_last_name, "
        "u.email AS assignee_email, "
        "(SELECT COALESCE(SUM(tr.minutes_spent), 0) FROM ticket_replies tr WHERE tr.ticket_id = pt.ticket_id "
        "AND tr.minutes_spent > 0) AS minutes_spent "
        "FROM project_tickets pt JOIN tickets t ON t.id = pt.ticket_id LEFT JOIN users u ON u.id = t.assigned_user_id "
        "WHERE pt.project_id = %s ORDER BY pt.position, pt.ticket_id",
        (project_id,),
    )
    tasks = []
    for row in rows or []:
        task = dict(row)
        task["ticket_id"] = int(task["ticket_id"])
        task["phase_id"] = int(task["phase_id"]) if task.get("phase_id") is not None else None
        task["estimate_hours"] = float(task["estimate_hours"]) if task.get("estimate_hours") is not None else None
        task["minutes_spent"] = int(task.get("minutes_spent") or 0)
        tasks.append(task)
    return tasks


async def get_task(ticket_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT ticket_id, project_id, phase_id, estimate_hours FROM project_tickets WHERE ticket_id = %s",
                             (ticket_id,))
    return dict(row) if row else None


async def add_task(project_id: int, ticket_id: int, *, phase_id: int | None, estimate_hours: float | None) -> None:
    row = await db.fetch_one("SELECT MAX(position) AS last FROM project_tickets WHERE project_id = %s", (project_id,))
    position = int((row or {}).get("last") or 0) + 1
    await db.execute(
        "INSERT INTO project_tickets (ticket_id, project_id, phase_id, estimate_hours, position) VALUES (%s, %s, %s, %s, %s)",
        (ticket_id, project_id, phase_id, estimate_hours, position),
    )


async def update_task(ticket_id: int, *, phase_id: int | None, estimate_hours: float | None) -> None:
    await db.execute("UPDATE project_tickets SET phase_id = %s, estimate_hours = %s WHERE ticket_id = %s",
                     (phase_id, estimate_hours, ticket_id))


async def remove_task(ticket_id: int) -> None:
    await db.execute("DELETE FROM project_tickets WHERE ticket_id = %s", (ticket_id,))


async def fixed_fee_ticket_ids(ticket_ids: Sequence[int]) -> set[int]:
    """Which of these tickets are tasks of fixed-fee projects."""

    ids = sorted({int(value) for value in ticket_ids if value})
    if not ids:
        return set()
    placeholders = ", ".join(["%s"] * len(ids))
    sql = "SELECT pt.ticket_id FROM project_tickets pt JOIN projects p ON p.id = pt.project_id WHERE p.billing_type = 'fixed_fee' AND pt.ticket_id IN (" + placeholders + ")"  # nosec B608 - placeholders only
    rows = await db.fetch_all(sql, tuple(ids))
    return {int(row["ticket_id"]) for row in rows or []}


async def project_totals(project_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """Task counts, estimates and time spent per project, for the list."""

    ids = sorted({int(value) for value in project_ids})
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    sql = (
        "SELECT pt.project_id, COUNT(*) AS tasks, "
        "SUM(CASE WHEN t.status IN ('resolved', 'closed') THEN 1 ELSE 0 END) AS done, "
        "COALESCE(SUM(pt.estimate_hours), 0) AS estimate_hours, "
        "COALESCE(SUM((SELECT COALESCE(SUM(tr.minutes_spent), 0) FROM ticket_replies tr WHERE tr.ticket_id = pt.ticket_id "
        "AND tr.minutes_spent > 0)), 0) AS minutes_spent "
        "FROM project_tickets pt JOIN tickets t ON t.id = pt.ticket_id WHERE pt.project_id IN (" + placeholders + ") "  # nosec B608 - placeholders only
        "GROUP BY pt.project_id"
    )
    rows = await db.fetch_all(sql, tuple(ids))
    return {int(row["project_id"]): {"tasks": int(row["tasks"] or 0), "done": int(row["done"] or 0),
                                     "estimate_hours": float(row["estimate_hours"] or 0),
                                     "minutes_spent": int(row["minutes_spent"] or 0)} for row in rows or []}


async def list_time_entries(project_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT tr.id, tr.ticket_id, tr.minutes_spent, tr.is_billable, tr.labour_type_id, lt.rate AS labour_rate "
        "FROM ticket_replies tr JOIN project_tickets pt ON pt.ticket_id = tr.ticket_id "
        "LEFT JOIN ticket_labour_types lt ON lt.id = tr.labour_type_id "
        "WHERE pt.project_id = %s AND tr.minutes_spent > 0",
        (project_id,),
    )
    return [dict(row) for row in rows or []]


# --------------------------------------------------------------------------- #
# Milestones
# --------------------------------------------------------------------------- #


def _milestone(row: dict[str, Any]) -> dict[str, Any]:
    record = dict(row)
    for key in ("id", "project_id", "phase_id", "invoice_id", "position"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    record["amount"] = float(record.get("amount") or 0)
    record["due_date"] = _as_date(record.get("due_date"))
    return record


async def list_milestones(project_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, project_id, phase_id, name, amount, due_date, status, invoice_id, invoice_number, invoiced_at, position "
        "FROM project_milestones WHERE project_id = %s ORDER BY position, id",
        (project_id,),
    )
    return [_milestone(row) for row in rows or []]


async def get_milestone(milestone_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT id, project_id, phase_id, name, amount, due_date, status, invoice_id, invoice_number, invoiced_at, position "
        "FROM project_milestones WHERE id = %s",
        (milestone_id,),
    )
    return _milestone(row) if row else None


async def create_milestone(project_id: int, values: dict[str, Any]) -> int:
    row = await db.fetch_one("SELECT MAX(position) AS last FROM project_milestones WHERE project_id = %s", (project_id,))
    position = int((row or {}).get("last") or 0) + 1
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO project_milestones (project_id, phase_id, name, amount, due_date, status, position) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (project_id, values.get("phase_id"), values["name"], values["amount"], values.get("due_date"),
         values.get("status") or "pending", position),
    ))


async def update_milestone(milestone_id: int, values: dict[str, Any]) -> None:
    await db.execute(
        "UPDATE project_milestones SET phase_id = %s, name = %s, amount = %s, due_date = %s, status = %s "
        "WHERE id = %s AND status <> 'invoiced'",
        (values.get("phase_id"), values["name"], values["amount"], values.get("due_date"),
         values.get("status") or "pending", milestone_id),
    )


async def claim_milestone_for_invoice(milestone_id: int) -> bool:
    """Mark a milestone as being invoiced; ``False`` when it already was."""

    claimed = await db.execute_rowcount(
        "UPDATE project_milestones SET status = 'invoicing' WHERE id = %s AND status IN ('pending', 'ready')",
        (milestone_id,),
    )
    return bool(claimed)


async def mark_milestone_invoiced(milestone_id: int, *, invoice_id: int | None, invoice_number: str | None,
                                  when: datetime | None, status: str = "invoiced") -> None:
    await db.execute(
        "UPDATE project_milestones SET status = %s, invoice_id = %s, invoice_number = %s, invoiced_at = %s WHERE id = %s",
        (status, invoice_id, invoice_number, when, milestone_id),
    )


async def delete_milestone(milestone_id: int) -> None:
    await db.execute("DELETE FROM project_milestones WHERE id = %s AND status <> 'invoiced'", (milestone_id,))
