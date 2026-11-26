"""Native project management.

A project belongs to a company and has phases, task tickets, a budget and,
when billed as a fixed fee, milestones.

* **Tasks are tickets.** Adding a task creates a ticket for the project's
  company (or links an existing one), so time entries, assignment, SLAs,
  appointments and automations work as they do for any ticket. A ticket
  belongs to at most one project, optionally in a phase, with an estimate.
* **Progress** is the share of task tickets resolved or closed; **hours** are
  the time logged on them against the budget (or the tasks' estimates).
* **Billing.** A *time and materials* project's ticket time is invoiced as
  usual. A *fixed fee* project's ticket time stays off ticket invoices
  (:func:`fixed_fee_ticket_ids`, used by both the local invoice generator and
  the Xero ticket sync) and the fee is billed by milestone: invoicing a
  milestone creates a draft invoice for the project's company with one line
  for the milestone, ready to review and send from the Invoices page.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from loguru import logger

from app.repositories import projects as repo

STATUSES = {
    "planning": "Planning",
    "active": "In progress",
    "on_hold": "On hold",
    "completed": "Completed",
    "cancelled": "Cancelled",
}
OPEN_STATUSES = ("planning", "active", "on_hold")
BILLING_TYPES = {
    "time_materials": ("Time and materials", "Ticket time is invoiced as usual."),
    "fixed_fee": ("Fixed fee", "Ticket time stays off invoices; bill the fee by milestone."),
}
MILESTONE_STATUSES = {"pending": "Not due", "ready": "Ready to invoice", "invoicing": "Invoicing",
                      "invoiced": "Invoiced"}
DONE_TICKET_STATUSES = {"resolved", "closed"}
MAX_HOURS = 100_000
MAX_AMOUNT = Decimal("100000000")


class ProjectError(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _decimal(value: Any) -> Decimal | None:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def hours_text(minutes: int | float | None) -> str:
    if minutes is None:
        return "—"
    return f"{minutes / 60:.1f}".rstrip("0").rstrip(".") + " h"


def person(record: Mapping[str, Any], prefix: str) -> str:
    name = " ".join(part for part in (record.get(f"{prefix}_first_name"), record.get(f"{prefix}_last_name")) if part)
    return name.strip() or str(record.get(f"{prefix}_email") or "")


# --------------------------------------------------------------------------- #
# Forms
# --------------------------------------------------------------------------- #


def parse_project_form(form: Mapping[str, Any]) -> dict[str, Any]:
    errors: dict[str, str] = {}
    name = " ".join(str(form.get("name") or "").split())[:255]
    if not name:
        errors["name"] = "Give the project a name, such as “Office move” or “Server replacement”."
    company_id = _int(form.get("company_id"))
    if company_id is None:
        errors["company_id"] = "Choose the company the project is for."
    status = str(form.get("status") or "planning")
    if status not in STATUSES:
        errors["status"] = "Choose the project's status."
    billing = str(form.get("billing_type") or "time_materials")
    if billing not in BILLING_TYPES:
        errors["billing_type"] = "Choose how the project is billed."
    hours = _decimal(form.get("budget_hours"))
    if str(form.get("budget_hours") or "").strip() and (hours is None or hours <= 0 or hours > MAX_HOURS):
        errors["budget_hours"] = "Enter the hours budgeted, such as 40, or leave it blank."
    amount = _decimal(form.get("budget_amount"))
    if str(form.get("budget_amount") or "").strip() and (amount is None or amount < 0 or amount > MAX_AMOUNT):
        errors["budget_amount"] = "Enter an amount, such as 12000."
    if billing == "fixed_fee" and amount is None and "budget_amount" not in errors:
        errors["budget_amount"] = "Enter the fixed fee."
    start = _date(form.get("start_date"))
    due = _date(form.get("due_date"))
    if str(form.get("start_date") or "").strip() and start is None:
        errors["start_date"] = "Enter a valid start date."
    if str(form.get("due_date") or "").strip() and due is None:
        errors["due_date"] = "Enter a valid due date."
    elif start and due and due < start:
        errors["due_date"] = "The due date must be on or after the start date."
    if errors:
        raise ProjectError(errors)
    return {
        "company_id": company_id, "name": name, "description": str(form.get("description") or "").strip()[:8000] or None,
        "status": status, "billing_type": billing, "budget_hours": hours, "budget_amount": amount,
        "start_date": start, "due_date": due, "manager_user_id": _int(form.get("manager_user_id")),
    }


def parse_milestone_form(form: Mapping[str, Any], *, phase_ids: Iterable[int]) -> dict[str, Any]:
    errors: dict[str, str] = {}
    name = " ".join(str(form.get("name") or "").split())[:255]
    if not name:
        errors["name"] = "Name the milestone, such as “Deposit” or “Go-live”."
    amount = _decimal(form.get("amount"))
    if amount is None or amount <= 0 or amount > MAX_AMOUNT:
        errors["amount"] = "Enter the amount to invoice."
    due = _date(form.get("due_date"))
    if str(form.get("due_date") or "").strip() and due is None:
        errors["due_date"] = "Enter a valid date."
    phase_id = _int(form.get("phase_id"))
    if phase_id is not None and phase_id not in set(phase_ids):
        errors["phase_id"] = "Choose one of the project's phases."
    status = "ready" if str(form.get("ready") or "").lower() in {"1", "on", "true", "yes"} else "pending"
    if errors:
        raise ProjectError(errors)
    return {"name": name, "amount": amount, "due_date": due, "phase_id": phase_id, "status": status}


def parse_estimate(value: Any) -> float | None:
    number = _decimal(value)
    if number is None:
        return None
    if number < 0 or number > MAX_HOURS:
        raise ProjectError({"estimate_hours": "Enter the estimate in hours, such as 2.5."})
    return float(number)


# --------------------------------------------------------------------------- #
# Summaries
# --------------------------------------------------------------------------- #


def summarise(project: Mapping[str, Any], totals: Mapping[str, Any] | None, *, today: date | None = None
              ) -> dict[str, Any]:
    today = today or _today()
    totals = totals or {"tasks": 0, "done": 0, "estimate_hours": 0.0, "minutes_spent": 0}
    tasks, done = int(totals["tasks"]), int(totals["done"])
    budget_hours = project.get("budget_hours") or (totals["estimate_hours"] or None)
    used_hours = totals["minutes_spent"] / 60
    hours_percent = round(100 * used_hours / budget_hours) if budget_hours else None
    flags = []
    if hours_percent is not None and hours_percent > 100:
        flags.append("over_budget")
    elif hours_percent is not None and hours_percent >= 85:
        flags.append("near_budget")
    due = project.get("due_date")
    if due and project.get("status") in OPEN_STATUSES:
        if due < today:
            flags.append("overdue")
        elif (due - today).days <= 14:
            flags.append("due_soon")
    return {
        **dict(project),
        "status_label": STATUSES.get(project.get("status"), project.get("status")),
        "billing_label": BILLING_TYPES.get(project.get("billing_type"), ("", ""))[0],
        "manager": person(project, "manager") if project.get("manager_user_id") else "",
        "tasks": tasks,
        "done": done,
        "progress": round(100 * done / tasks) if tasks else 0,
        "used_text": hours_text(totals["minutes_spent"]),
        "budget_text": hours_text(budget_hours * 60) if budget_hours else "No budget",
        "budget_source": "budget" if project.get("budget_hours") else ("estimates" if budget_hours else None),
        "hours_percent": hours_percent,
        "hours_meter": min(100, hours_percent or 0),
        "flags": flags,
    }


async def list_summaries(*, company_id: int | None = None) -> list[dict[str, Any]]:
    projects = await repo.list_projects(company_id=company_id)
    totals = await repo.project_totals([project["id"] for project in projects])
    return [summarise(project, totals.get(project["id"])) for project in projects]


def financials(project: Mapping[str, Any], entries: Sequence[Mapping[str, Any]],
               milestones: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Value of the time logged (hours × labour rate) against the budget or fee."""

    value = sum(int(entry.get("minutes_spent") or 0) / 60 * float(entry.get("labour_rate") or 0) for entry in entries)
    billable_minutes = sum(int(entry.get("minutes_spent") or 0) for entry in entries if entry.get("is_billable"))
    amount = float(project.get("budget_amount") or 0)
    invoiced = sum(item["amount"] for item in milestones if item.get("status") == "invoiced")
    planned = sum(item["amount"] for item in milestones)
    result = {
        "time_value": round(value, 2),
        "billable_text": hours_text(billable_minutes),
        "budget_amount": amount or None,
        "milestones_invoiced": round(invoiced, 2),
        "milestones_planned": round(planned, 2),
        "milestones_unplanned": round(amount - planned, 2) if amount else None,
    }
    if project.get("billing_type") == "fixed_fee" and amount:
        result["margin"] = round(amount - value, 2)
        result["margin_percent"] = round(100 * (amount - value) / amount) if amount else None
    elif amount:
        result["budget_left"] = round(amount - value, 2)
    return result


async def project_detail(project: Mapping[str, Any]) -> dict[str, Any]:
    phases = await repo.list_phases(int(project["id"]))
    tasks = await repo.list_tasks(int(project["id"]))
    milestones = await repo.list_milestones(int(project["id"]))
    entries = await repo.list_time_entries(int(project["id"]))
    totals = {
        "tasks": len(tasks),
        "done": sum(1 for task in tasks if str(task.get("status") or "").lower() in DONE_TICKET_STATUSES),
        "estimate_hours": sum(task.get("estimate_hours") or 0 for task in tasks),
        "minutes_spent": sum(task["minutes_spent"] for task in tasks),
    }
    summary = summarise(project, totals)
    for task in tasks:
        task["done"] = str(task.get("status") or "").lower() in DONE_TICKET_STATUSES
        task["assignee"] = person(task, "assignee")
        task["spent_text"] = hours_text(task["minutes_spent"])
        task["over_estimate"] = bool(task.get("estimate_hours")) and task["minutes_spent"] / 60 > task["estimate_hours"]
    groups = []
    for phase in phases:
        items = [task for task in tasks if task.get("phase_id") == phase["id"]]
        groups.append({"phase": phase, "tasks": items, "done": sum(1 for task in items if task["done"]),
                       "spent_text": hours_text(sum(task["minutes_spent"] for task in items)),
                       "estimate_text": hours_text(sum(task.get("estimate_hours") or 0 for task in items) * 60)})
    loose = [task for task in tasks if task.get("phase_id") not in {phase["id"] for phase in phases}]
    if loose or not phases:
        groups.append({"phase": None, "tasks": loose, "done": sum(1 for task in loose if task["done"]),
                       "spent_text": hours_text(sum(task["minutes_spent"] for task in loose)),
                       "estimate_text": hours_text(sum(task.get("estimate_hours") or 0 for task in loose) * 60)})
    return {"project": summary, "phases": phases, "groups": groups, "milestones": milestones,
            "financials": financials(project, entries, milestones)}


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #


async def create_task_ticket(project: Mapping[str, Any], *, subject: str, description: str | None,
                             assigned_user_id: int | None, phase_id: int | None, estimate_hours: float | None,
                             user: Mapping[str, Any] | None) -> dict[str, Any]:
    from app.services import tickets as tickets_service

    subject = " ".join(str(subject or "").split())[:255]
    if not subject:
        raise ProjectError({"subject": "Describe the task, such as “Install new switches”."})
    ticket = await tickets_service.create_ticket(
        subject=subject,
        description=description or f"Task for project {project['name']}.",
        requester_id=None,
        company_id=int(project["company_id"]),
        assigned_user_id=assigned_user_id,
        priority="normal",
        status="open",
        category=None,
        module_slug="projects",
        external_reference=None,
        initial_reply_author_id=int(user["id"]) if user and user.get("id") else None,
    )
    await repo.add_task(int(project["id"]), int(ticket["id"]), phase_id=phase_id, estimate_hours=estimate_hours)
    return ticket


async def link_ticket(project: Mapping[str, Any], ticket_id: int, *, phase_id: int | None,
                      estimate_hours: float | None) -> dict[str, Any]:
    from app.repositories import tickets as tickets_repo

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise ProjectError({"ticket_id": "That ticket doesn't exist."})
    if ticket.get("company_id") is None or int(ticket["company_id"]) != int(project["company_id"]):
        raise ProjectError({"ticket_id": "Only tickets for the project's company can be added."})
    existing = await repo.get_task(ticket_id)
    if existing:
        message = ("That ticket is already in this project." if int(existing["project_id"]) == int(project["id"])
                   else "That ticket is already in another project.")
        raise ProjectError({"ticket_id": message})
    await repo.add_task(int(project["id"]), ticket_id, phase_id=phase_id, estimate_hours=estimate_hours)
    return ticket


async def fixed_fee_ticket_ids(ticket_ids: Iterable[Any]) -> set[int]:
    """Tickets whose time must stay off ticket invoices (tasks of fixed-fee projects).

    Never raises: if projects can't be read, nothing is excluded, so a
    projects problem can't stop invoicing."""

    ids = []
    for value in ticket_ids:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return set()
    try:
        return await repo.fixed_fee_ticket_ids(ids)
    except Exception as exc:  # noqa: BLE001 - invoicing must not fail because of projects
        logger.warning("Could not check fixed-fee project tickets: {}", exc)
        return set()


# --------------------------------------------------------------------------- #
# Milestones
# --------------------------------------------------------------------------- #


def milestone_line(project: Mapping[str, Any], milestone: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "Description": f"{project['name']}: {milestone['name']}",
        "Quantity": 1.0,
        "UnitAmount": float(milestone["amount"]),
        "ItemCode": "",
    }


async def invoice_milestone(project: Mapping[str, Any], milestone: Mapping[str, Any], *,
                            user: Mapping[str, Any] | None) -> dict[str, Any]:
    """Create a draft invoice for one milestone. Raises :class:`ProjectError`."""

    from app.services import invoice_generator

    if milestone.get("status") == "invoiced":
        raise ProjectError({"milestone": "That milestone has already been invoiced."})
    if not await repo.claim_milestone_for_invoice(int(milestone["id"])):
        raise ProjectError({"milestone": "That milestone is already being invoiced."})
    try:
        result = await invoice_generator.generate_invoice(
            int(project["company_id"]),
            created_by=int(user["id"]) if user and user.get("id") else None,
            include_recurring_items=False,
            recurring_line_items=[milestone_line(project, milestone)],
            include_ticket_items=False,
        )
    except Exception as exc:  # noqa: BLE001 - put the milestone back so it can be retried
        await repo.mark_milestone_invoiced(int(milestone["id"]), invoice_id=None, invoice_number=None, when=None,
                                           status="ready")
        raise ProjectError({"milestone": f"The invoice could not be created: {exc}"}) from exc
    if result.get("status") != "succeeded":
        await repo.mark_milestone_invoiced(int(milestone["id"]), invoice_id=None, invoice_number=None, when=None,
                                           status="ready")
        raise ProjectError({"milestone": f"The invoice could not be created: {result.get('reason') or 'unknown error'}."})
    await repo.mark_milestone_invoiced(int(milestone["id"]), invoice_id=int(result["invoice_id"]),
                                       invoice_number=str(result.get("invoice_number") or "") or None,
                                       when=datetime.now(timezone.utc).replace(tzinfo=None))
    return result
