"""Routes for native project management (:mod:`app.services.projects`)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.datastructures import ImmutableMultiDict

from app.api.dependencies.auth import require_helpdesk_technician, require_super_admin
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import projects as repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import projects

router = APIRouter(tags=["Projects"])

PAGE_PATH = "/admin/projects"


def _main():
    from app import main as main_module

    return main_module


def _optional_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _plain(values: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in values.items():
        if isinstance(value, (date,)):
            out[key] = value.isoformat()
        elif isinstance(value, Decimal):
            out[key] = str(value)
        else:
            out[key] = value
    return out


# The role "Technician" switch (``menu.admin.technician``) grants this
# permission. ``helpdesk.technician`` is not used: any role with ticket access
# carries it, so it would offer customer users as project managers and assignees.
TECHNICIAN_PERMISSION = "company.switch_all"


async def _technicians() -> list[dict[str, Any]]:
    """Return Super Admins and users whose active role has the Technician switch on."""
    users = await membership_repo.list_users_with_permission(TECHNICIAN_PERMISSION)
    items = []
    for user in users:
        name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part).strip()
        items.append({"id": int(user["id"]), "name": name or str(user.get("email") or f"User {user['id']}")})
    return sorted(items, key=lambda item: item["name"].casefold())


async def _project_or_404(project_id: int) -> dict[str, Any]:
    project = await repo.get_project(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


async def _helpdesk(request: Request):
    user, redirect = await _main()._require_helpdesk_page(request)
    return user, redirect


def _errors_text(exc: projects.ProjectError) -> str:
    return " ".join(exc.errors.values())


def _editor_state(project: dict[str, Any] | None, *, company_id: int | None = None) -> dict[str, Any]:
    if project is None:
        return {"id": None, "company_id": company_id, "status": "planning", "billing_type": "time_materials"}
    return {
        **project,
        "budget_hours": f"{project['budget_hours']:g}" if project.get("budget_hours") is not None else "",
        "budget_amount": f"{project['budget_amount']:.2f}" if project.get("budget_amount") is not None else "",
        "start_date": project["start_date"].isoformat() if project.get("start_date") else "",
        "due_date": project["due_date"].isoformat() if project.get("due_date") else "",
    }


async def _render_list(request: Request, user: dict[str, Any], *, company_id: int | None, show: str,
                       editing: dict[str, Any] | None = None, errors: dict[str, str] | None = None,
                       status_code: int = status.HTTP_200_OK):
    items = await projects.list_summaries(company_id=company_id)
    open_items = [item for item in items if item["status"] in projects.OPEN_STATUSES]
    visible = open_items if show != "all" else items
    response = await _main()._render_template(
        "projects/list.html",
        request,
        user,
        extra={
            "title": "Projects",
            "items": visible,
            "show": show,
            "counts": {
                "open": len(open_items),
                "over": sum(1 for item in open_items if "over_budget" in item["flags"]),
                "overdue": sum(1 for item in open_items if "overdue" in item["flags"]),
                "due_soon": sum(1 for item in open_items if "due_soon" in item["flags"]),
            },
            "companies": await company_repo.list_companies(),
            "selected_company_id": company_id,
            "technicians": await _technicians(),
            "statuses": projects.STATUSES,
            "billing_types": projects.BILLING_TYPES,
            "editing": editing,
            "errors": errors or {},
        },
    )
    response.status_code = status_code
    return response


@router.get(PAGE_PATH, response_class=HTMLResponse, summary="Projects")
async def projects_page(request: Request, company_id: str | None = Query(default=None),
                        show: str = Query(default="open"), project: int | None = Query(default=None, ge=1),
                        new: bool = Query(default=False)):
    user, redirect = await _helpdesk(request)
    if redirect:
        return redirect
    editing = None
    if project is not None:
        editing = _editor_state(await _project_or_404(project))
    elif new:
        editing = _editor_state(None, company_id=_optional_int(company_id))
    return await _render_list(request, user, company_id=_optional_int(company_id),
                              show="all" if show == "all" else "open", editing=editing)


async def _save_project(request: Request, project_id: int | None):
    user, redirect = await _helpdesk(request)
    if redirect:
        return redirect
    previous = await _project_or_404(project_id) if project_id is not None else None
    form = await request.form()
    try:
        values = projects.parse_project_form(form)
        if not await company_repo.get_company_by_id(values["company_id"]):
            raise projects.ProjectError({"company_id": "Choose the company the project is for."})
        technician_ids = {item["id"] for item in await _technicians()}
        if values["manager_user_id"] is not None and values["manager_user_id"] not in technician_ids:
            raise projects.ProjectError({"manager_user_id": "Choose a technician to manage the project."})
        if previous and int(previous["company_id"]) != int(values["company_id"]) and await repo.list_tasks(project_id):
            raise projects.ProjectError({"company_id": "Remove the project's tasks before moving it to another company."})
    except projects.ProjectError as exc:
        posted = {key: form.get(key) for key in ("name", "description", "status", "billing_type", "budget_hours",
                                                  "budget_amount", "start_date", "due_date")}
        posted.update(id=project_id, company_id=_optional_int(form.get("company_id")),
                      manager_user_id=_optional_int(form.get("manager_user_id")))
        return await _render_list(request, user, company_id=None, show="open", editing=posted, errors=exc.errors,
                                  status_code=status.HTTP_400_BAD_REQUEST)
    if project_id is None:
        project_id = await repo.create_project(values, user_id=user.get("id"))
    else:
        await repo.update_project(project_id, values)
    await audit_service.log_action(action="project.save", user_id=user.get("id"), entity_type="project",
                                   entity_id=project_id, previous_value=_plain(previous) if previous else None,
                                   new_value=_plain(values), request=request)
    return flash_redirect(f"{PAGE_PATH}/{project_id}", f"Saved {values['name']}.", "success")


@router.post(PAGE_PATH, response_class=HTMLResponse, summary="Create a project")
async def create_project(request: Request):
    return await _save_project(request, None)


@router.post(PAGE_PATH + "/{project_id:int}", response_class=HTMLResponse, summary="Update a project")
async def update_project(project_id: int, request: Request):
    return await _save_project(request, project_id)


@router.post(PAGE_PATH + "/{project_id:int}/delete", response_class=HTMLResponse, summary="Delete a project")
async def delete_project(project_id: int, request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    project = await _project_or_404(project_id)
    if any(item["status"] == "invoiced" for item in await repo.list_milestones(project_id)):
        return flash_redirect(f"{PAGE_PATH}/{project_id}",
                              "This project has invoiced milestones, so it can't be deleted. Mark it completed or cancelled instead.",
                              "error")
    await repo.delete_project(project_id)
    await audit_service.log_action(action="project.delete", user_id=user.get("id"), entity_type="project",
                                   entity_id=project_id, previous_value=_plain(project), request=request)
    return flash_redirect(PAGE_PATH, f"Deleted {project['name']}. Its tickets are kept.", "success")


@router.get(PAGE_PATH + "/{project_id:int}", response_class=HTMLResponse, summary="A project")
async def project_page(project_id: int, request: Request):
    user, redirect = await _helpdesk(request)
    if redirect:
        return redirect
    project = await _project_or_404(project_id)
    detail = await projects.project_detail(project)
    return await _main()._render_template(
        "projects/detail.html",
        request,
        user,
        extra={
            "title": project["name"],
            "detail": detail,
            "technicians": await _technicians(),
            "milestone_statuses": projects.MILESTONE_STATUSES,
            "can_invoice": bool(user.get("is_super_admin")),
            "is_super_admin": bool(user.get("is_super_admin")),
            "hours_text": projects.hours_text,
        },
    )


# --------------------------------------------------------------------------- #
# Phases
# --------------------------------------------------------------------------- #


def _back(project_id: int, anchor: str = "") -> str:
    return f"{PAGE_PATH}/{project_id}{anchor}"


async def _phase_for(project_id: int, phase_id: int) -> dict[str, Any]:
    phase = await repo.get_phase(phase_id)
    if not phase or int(phase["project_id"]) != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Phase not found")
    return phase


@router.post(PAGE_PATH + "/{project_id:int}/phases", response_class=HTMLResponse, summary="Add a phase")
async def add_phase(project_id: int, request: Request):
    user, redirect = await _helpdesk(request)
    if redirect:
        return redirect
    await _project_or_404(project_id)
    form = await request.form()
    name = " ".join(str(form.get("name") or "").split())[:255]
    if not name:
        return flash_redirect(_back(project_id, "#project-tasks"), "Name the phase, such as “Planning” or “Rollout”.", "error")
    due = projects._date(form.get("due_date"))
    phase_id = await repo.create_phase(project_id, name, due)
    await audit_service.log_action(action="project.phase.add", user_id=user.get("id"), entity_type="project_phase",
                                   entity_id=phase_id, new_value={"project_id": project_id, "name": name}, request=request)
    return flash_redirect(_back(project_id, "#project-tasks"), f"Added the {name} phase.", "success")


@router.post(PAGE_PATH + "/{project_id:int}/phases/{phase_id:int}", response_class=HTMLResponse,
             summary="Rename, move or delete a phase")
async def change_phase(project_id: int, phase_id: int, request: Request):
    user, redirect = await _helpdesk(request)
    if redirect:
        return redirect
    phase = await _phase_for(project_id, phase_id)
    form = await request.form()
    action = str(form.get("action") or "save")
    if action == "delete":
        await repo.delete_phase(phase_id)
        message = f"Deleted the {phase['name']} phase. Its tasks are kept, without a phase."
    elif action in ("up", "down"):
        phases = await repo.list_phases(project_id)
        index = next(i for i, item in enumerate(phases) if item["id"] == phase_id)
        other = index - 1 if action == "up" else index + 1
        if 0 <= other < len(phases):
            phases[index], phases[other] = phases[other], phases[index]
            for position, item in enumerate(phases, start=1):
                await repo.move_phase(item["id"], position)
        message = f"Moved the {phase['name']} phase."
    else:
        name = " ".join(str(form.get("name") or "").split())[:255] or phase["name"]
        await repo.update_phase(phase_id, name, projects._date(form.get("due_date")))
        message = f"Saved the {name} phase."
    await audit_service.log_action(action=f"project.phase.{action}", user_id=user.get("id"),
                                   entity_type="project_phase", entity_id=phase_id, request=request)
    return flash_redirect(_back(project_id, "#project-tasks"), message, "success")


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #


async def _phase_id(project_id: int, value: Any) -> int | None:
    phase_id = _optional_int(value)
    if phase_id is not None:
        await _phase_for(project_id, phase_id)
    return phase_id


@router.post(PAGE_PATH + "/{project_id:int}/tasks", response_class=HTMLResponse, summary="Add a task")
async def add_task(project_id: int, request: Request):
    user, redirect = await _helpdesk(request)
    if redirect:
        return redirect
    project = await _project_or_404(project_id)
    form = await request.form()
    try:
        phase_id = await _phase_id(project_id, form.get("phase_id"))
        estimate = projects.parse_estimate(form.get("estimate_hours"))
        if str(form.get("mode") or "new") == "existing":
            ticket_id = _optional_int(str(form.get("ticket_id") or "").lstrip("#"))
            if ticket_id is None:
                raise projects.ProjectError({"ticket_id": "Enter the ticket's ID."})
            ticket = await projects.link_ticket(project, ticket_id, phase_id=phase_id, estimate_hours=estimate)
            message = f"Added ticket #{ticket.get('ticket_number') or ticket_id} to the project."
        else:
            assignee = _optional_int(form.get("assigned_user_id"))
            if assignee is not None and assignee not in {item["id"] for item in await _technicians()}:
                raise projects.ProjectError({"assigned_user_id": "Choose a technician."})
            ticket = await projects.create_task_ticket(
                project, subject=str(form.get("subject") or ""), description=str(form.get("description") or "").strip() or None,
                assigned_user_id=assignee, phase_id=phase_id, estimate_hours=estimate, user=user)
            message = f"Created task ticket #{ticket.get('ticket_number') or ticket['id']}."
    except projects.ProjectError as exc:
        return flash_redirect(_back(project_id, "#project-tasks"), _errors_text(exc), "error")
    await audit_service.log_action(action="project.task.add", user_id=user.get("id"), entity_type="project",
                                   entity_id=project_id, new_value={"ticket_id": int(ticket["id"]), "phase_id": phase_id},
                                   request=request)
    return flash_redirect(_back(project_id, "#project-tasks"), message, "success")


@router.post(PAGE_PATH + "/{project_id:int}/tasks/{ticket_id:int}", response_class=HTMLResponse,
             summary="Change or remove a task")
async def change_task(project_id: int, ticket_id: int, request: Request):
    user, redirect = await _helpdesk(request)
    if redirect:
        return redirect
    task = await repo.get_task(ticket_id)
    if not task or int(task["project_id"]) != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    form = await request.form()
    if str(form.get("action") or "save") == "remove":
        await repo.remove_task(ticket_id)
        message = "Removed the ticket from the project. The ticket itself is kept."
    else:
        try:
            phase_id = await _phase_id(project_id, form.get("phase_id"))
            estimate = projects.parse_estimate(form.get("estimate_hours"))
        except projects.ProjectError as exc:
            return flash_redirect(_back(project_id, "#project-tasks"), _errors_text(exc), "error")
        await repo.update_task(ticket_id, phase_id=phase_id, estimate_hours=estimate)
        message = "Saved the task."
    await audit_service.log_action(action="project.task.change", user_id=user.get("id"), entity_type="project",
                                   entity_id=project_id, new_value={"ticket_id": ticket_id, "action": form.get("action")},
                                   request=request)
    return flash_redirect(_back(project_id, "#project-tasks"), message, "success")


# --------------------------------------------------------------------------- #
# Milestones
# --------------------------------------------------------------------------- #


async def _milestone_for(project_id: int, milestone_id: int) -> dict[str, Any]:
    milestone = await repo.get_milestone(milestone_id)
    if not milestone or int(milestone["project_id"]) != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Milestone not found")
    return milestone


@router.post(PAGE_PATH + "/{project_id:int}/milestones", response_class=HTMLResponse, summary="Add a milestone")
async def add_milestone(project_id: int, request: Request):
    user, redirect = await _helpdesk(request)
    if redirect:
        return redirect
    await _project_or_404(project_id)
    form = await request.form()
    try:
        values = projects.parse_milestone_form(form, phase_ids=[item["id"] for item in await repo.list_phases(project_id)])
    except projects.ProjectError as exc:
        return flash_redirect(_back(project_id, "#project-milestones"), _errors_text(exc), "error")
    milestone_id = await repo.create_milestone(project_id, values)
    await audit_service.log_action(action="project.milestone.add", user_id=user.get("id"), entity_type="project_milestone",
                                   entity_id=milestone_id, new_value=_plain(values), request=request)
    return flash_redirect(_back(project_id, "#project-milestones"), f"Added the {values['name']} milestone.", "success")


@router.post(PAGE_PATH + "/{project_id:int}/milestones/{milestone_id:int}", response_class=HTMLResponse,
             summary="Change or delete a milestone")
async def change_milestone(project_id: int, milestone_id: int, request: Request):
    user, redirect = await _helpdesk(request)
    if redirect:
        return redirect
    milestone = await _milestone_for(project_id, milestone_id)
    if milestone["status"] in ("invoiced", "invoicing"):
        return flash_redirect(_back(project_id, "#project-milestones"),
                              "An invoiced milestone can't be changed. Adjust the invoice instead.", "error")
    form = await request.form()
    if str(form.get("action") or "save") == "delete":
        await repo.delete_milestone(milestone_id)
        message = f"Deleted the {milestone['name']} milestone."
    else:
        try:
            values = projects.parse_milestone_form(form, phase_ids=[item["id"] for item in await repo.list_phases(project_id)])
        except projects.ProjectError as exc:
            return flash_redirect(_back(project_id, "#project-milestones"), _errors_text(exc), "error")
        await repo.update_milestone(milestone_id, values)
        message = f"Saved the {values['name']} milestone."
    await audit_service.log_action(action="project.milestone.change", user_id=user.get("id"),
                                   entity_type="project_milestone", entity_id=milestone_id, request=request)
    return flash_redirect(_back(project_id, "#project-milestones"), message, "success")


@router.post(PAGE_PATH + "/{project_id:int}/milestones/{milestone_id:int}/invoice", response_class=HTMLResponse,
             summary="Invoice a milestone")
async def invoice_milestone(project_id: int, milestone_id: int, request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    project = await _project_or_404(project_id)
    milestone = await _milestone_for(project_id, milestone_id)
    try:
        result = await projects.invoice_milestone(project, milestone, user=user)
    except projects.ProjectError as exc:
        return flash_redirect(_back(project_id, "#project-milestones"), _errors_text(exc), "error")
    await audit_service.log_action(action="project.milestone.invoice", user_id=user.get("id"),
                                   entity_type="project_milestone", entity_id=milestone_id,
                                   new_value={"invoice_id": result.get("invoice_id"),
                                              "invoice_number": result.get("invoice_number")}, request=request)
    return flash_redirect(_back(project_id, "#project-milestones"),
                          f"Created draft invoice {result.get('invoice_number')} for {milestone['name']}. "
                          "Review and send it from Invoices.", "success")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


class ProjectIn(BaseModel):
    company_id: int
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=8000)
    status: str = Field(default="planning", description="planning, active, on_hold, completed or cancelled")
    billing_type: str = Field(default="time_materials", description="time_materials or fixed_fee")
    budget_hours: float | None = Field(default=None, gt=0)
    budget_amount: float | None = Field(default=None, ge=0, description="Budget, or the fee for fixed-fee projects")
    start_date: date | None = None
    due_date: date | None = None
    manager_user_id: int | None = None


class TaskIn(BaseModel):
    ticket_id: int | None = Field(default=None, description="Add an existing ticket; omit to create one")
    subject: str | None = Field(default=None, max_length=255)
    description: str | None = None
    assigned_user_id: int | None = None
    phase_id: int | None = None
    estimate_hours: float | None = Field(default=None, ge=0)


def _api_project(item: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "company_id", "company_name", "name", "description", "status", "billing_type", "budget_hours",
            "budget_amount", "manager_user_id", "tasks", "done", "progress", "hours_percent", "flags")
    payload = {key: item.get(key) for key in keys}
    payload["start_date"] = item["start_date"].isoformat() if item.get("start_date") else None
    payload["due_date"] = item["due_date"].isoformat() if item.get("due_date") else None
    return payload


@router.get("/api/projects", summary="Projects")
async def api_list(company_id: int | None = Query(default=None),
                   _: dict = Depends(require_helpdesk_technician)) -> list[dict[str, Any]]:
    return [_api_project(item) for item in await projects.list_summaries(company_id=company_id)]


@router.get("/api/projects/{project_id}", summary="A project with its phases, tasks and milestones")
async def api_get(project_id: int, _: dict = Depends(require_helpdesk_technician)) -> dict[str, Any]:
    detail = await projects.project_detail(await _project_or_404(project_id))
    payload = _api_project(detail["project"])
    payload["phases"] = [{**phase, "due_date": phase["due_date"].isoformat() if phase.get("due_date") else None}
                         for phase in detail["phases"]]
    payload["tasks"] = [{key: task.get(key) for key in ("ticket_id", "ticket_number", "subject", "status", "phase_id",
                                                         "estimate_hours", "minutes_spent", "assigned_user_id", "done")}
                        for group in detail["groups"] for task in group["tasks"]]
    payload["milestones"] = [{**item, "due_date": item["due_date"].isoformat() if item.get("due_date") else None,
                              "invoiced_at": item["invoiced_at"].isoformat() if hasattr(item.get("invoiced_at"), "isoformat") else None}
                             for item in detail["milestones"]]
    payload["financials"] = detail["financials"]
    return payload


def _form_from(payload: BaseModel) -> ImmutableMultiDict:
    return ImmutableMultiDict([(key, value.isoformat() if isinstance(value, date) else str(value))
                               for key, value in payload.model_dump().items() if value is not None])


async def _api_save(payload: ProjectIn, request: Request, user: dict, project_id: int | None) -> dict[str, Any]:
    previous = await _project_or_404(project_id) if project_id is not None else None
    try:
        values = projects.parse_project_form(_form_from(payload))
    except projects.ProjectError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    if not await company_repo.get_company_by_id(values["company_id"]):
        raise HTTPException(status_code=422, detail={"company_id": "Unknown company."})
    if project_id is None:
        project_id = await repo.create_project(values, user_id=user.get("id"))
    else:
        await repo.update_project(project_id, values)
    await audit_service.log_action(action="project.save", user_id=user.get("id"), entity_type="project",
                                   entity_id=project_id, previous_value=_plain(previous) if previous else None,
                                   new_value=_plain(values), request=request)
    return await api_get(project_id, user)


@router.post("/api/projects", summary="Create a project", status_code=status.HTTP_201_CREATED)
async def api_create(payload: ProjectIn, request: Request, user: dict = Depends(require_helpdesk_technician)):
    return await _api_save(payload, request, user, None)


@router.put("/api/projects/{project_id}", summary="Update a project")
async def api_update(project_id: int, payload: ProjectIn, request: Request,
                     user: dict = Depends(require_helpdesk_technician)):
    return await _api_save(payload, request, user, project_id)


@router.delete("/api/projects/{project_id}", summary="Delete a project", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete(project_id: int, request: Request, user: dict = Depends(require_super_admin)) -> None:
    project = await _project_or_404(project_id)
    if any(item["status"] == "invoiced" for item in await repo.list_milestones(project_id)):
        raise HTTPException(status_code=409, detail="The project has invoiced milestones.")
    await repo.delete_project(project_id)
    await audit_service.log_action(action="project.delete", user_id=user.get("id"), entity_type="project",
                                   entity_id=project_id, previous_value=_plain(project), request=request)


@router.post("/api/projects/{project_id}/tasks", summary="Add a task (new or existing ticket)",
             status_code=status.HTTP_201_CREATED)
async def api_add_task(project_id: int, payload: TaskIn, request: Request,
                       user: dict = Depends(require_helpdesk_technician)) -> dict[str, Any]:
    project = await _project_or_404(project_id)
    try:
        phase_id = await _phase_id(project_id, payload.phase_id)
        if payload.ticket_id:
            ticket = await projects.link_ticket(project, payload.ticket_id, phase_id=phase_id,
                                                estimate_hours=payload.estimate_hours)
        else:
            ticket = await projects.create_task_ticket(project, subject=payload.subject or "", description=payload.description,
                                                       assigned_user_id=payload.assigned_user_id, phase_id=phase_id,
                                                       estimate_hours=payload.estimate_hours, user=user)
    except projects.ProjectError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    await audit_service.log_action(action="project.task.add", user_id=user.get("id"), entity_type="project",
                                   entity_id=project_id, new_value={"ticket_id": int(ticket["id"])}, request=request)
    return {"project_id": project_id, "ticket_id": int(ticket["id"]), "phase_id": phase_id}


@router.post("/api/projects/{project_id}/milestones/{milestone_id}/invoice", summary="Invoice a milestone")
async def api_invoice_milestone(project_id: int, milestone_id: int, request: Request,
                                user: dict = Depends(require_super_admin)) -> dict[str, Any]:
    project = await _project_or_404(project_id)
    milestone = await _milestone_for(project_id, milestone_id)
    try:
        result = await projects.invoice_milestone(project, milestone, user=user)
    except projects.ProjectError as exc:
        raise HTTPException(status_code=409, detail=exc.errors) from exc
    await audit_service.log_action(action="project.milestone.invoice", user_id=user.get("id"),
                                   entity_type="project_milestone", entity_id=milestone_id,
                                   new_value={"invoice_id": result.get("invoice_id")}, request=request)
    return {"milestone_id": milestone_id, "invoice_id": result.get("invoice_id"),
            "invoice_number": result.get("invoice_number")}


__all__ = ["router"]
