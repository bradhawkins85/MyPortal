from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies.auth import get_current_session, get_current_user
from app.repositories import processes as repo
from app.repositories import user_companies as user_company_repo
from app.security.session import SessionData
from app.services import audit as audit_service

router = APIRouter(prefix="/api/processes", tags=["Processes"])


class StepInput(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    instructions: str | None = Field(default=None, max_length=20000)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return value.strip()


class TemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=191)
    description: str | None = Field(default=None, max_length=20000)
    steps: list[StepInput] = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class RunInput(BaseModel):
    template_id: int = Field(gt=0)
    asset_id: int | None = Field(default=None, gt=0)
    ticket_id: int | None = Field(default=None, gt=0)
    assignee_id: int | None = Field(default=None, gt=0)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    due_at: datetime | None = None


class StepUpdate(BaseModel):
    status: Literal["pending", "in_progress", "completed", "skipped"]
    notes: str | None = Field(default=None, max_length=20000)


class RunStatusUpdate(BaseModel):
    status: Literal["pending", "in_progress", "completed", "cancelled"]


async def access_context(
    request: Request,
    user: dict = Depends(get_current_user),
    session: SessionData = Depends(get_current_session),
) -> tuple[dict, int, dict | None]:
    company_id = session.active_company_id or user.get("company_id")
    if company_id is None:
        raise HTTPException(status_code=400, detail="An active company is required")
    company_id = int(company_id)
    membership = await user_company_repo.get_user_company(int(user["id"]), company_id)
    from app import main as main_module
    if not main_module._membership_menu_can(user, membership, "menu.assets"):
        raise HTTPException(status_code=403, detail="Process access required")
    return user, company_id, membership


def require_write(context: tuple[dict, int, dict | None]) -> tuple[dict, int]:
    user, company_id, membership = context
    from app import main as main_module
    if not main_module._membership_menu_can(user, membership, "menu.assets", write=True):
        raise HTTPException(status_code=403, detail="Process write access required")
    return user, company_id


@router.get("/templates", summary="List process templates for the active company")
async def list_templates(context: tuple[dict, int, dict | None] = Depends(access_context)):
    return await repo.list_templates(context[1])


@router.post("/templates", status_code=status.HTTP_201_CREATED, summary="Create a versioned process template")
async def create_template(payload: TemplateInput, request: Request,
                          context: tuple[dict, int, dict | None] = Depends(access_context)):
    user, company_id = require_write(context)
    template_id = await repo.create_template(company_id, payload.name, payload.description,
                                             [step.model_dump() for step in payload.steps], int(user["id"]))
    await audit_service.record(action="process.template.create", request=request, user_id=int(user["id"]),
                               entity_type="process_template", entity_id=template_id,
                               after={"company_id": company_id, "name": payload.name, "version": 1})
    return {"id": template_id, "version": 1}


@router.put("/templates/{template_id}", summary="Publish a new immutable template version")
async def update_template(template_id: int, payload: TemplateInput, request: Request,
                          context: tuple[dict, int, dict | None] = Depends(access_context)):
    user, company_id = require_write(context)
    template = await repo.get_template(company_id, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Process template not found")
    version = await repo.add_version(template_id, int(template["current_version"]), payload.name,
                                     payload.description, [step.model_dump() for step in payload.steps])
    await audit_service.record(action="process.template.version", request=request, user_id=int(user["id"]),
                               entity_type="process_template", entity_id=template_id,
                               before={"version": template["current_version"]}, after={"version": version})
    return {"id": template_id, "version": version}


@router.get("/runs", summary="List execution runs for the active company")
async def list_runs(context: tuple[dict, int, dict | None] = Depends(access_context)):
    return await repo.list_runs(context[1])


@router.post("/runs", status_code=status.HTTP_201_CREATED, summary="Start a run with an immutable step snapshot")
async def start_run(payload: RunInput, request: Request,
                    context: tuple[dict, int, dict | None] = Depends(access_context)):
    user, company_id = require_write(context)
    template = await repo.get_template(company_id, payload.template_id)
    if not template or not template.get("is_active"):
        raise HTTPException(status_code=404, detail="Process template not found")
    for table, record_id, label in (("assets", payload.asset_id, "Asset"), ("tickets", payload.ticket_id, "Ticket")):
        if record_id and not await repo.company_reference_exists(table, company_id, record_id):
            raise HTTPException(status_code=404, detail=f"{label} not found")
    if payload.assignee_id:
        membership = await user_company_repo.get_user_company(payload.assignee_id, company_id)
        if not membership:
            raise HTTPException(status_code=422, detail="Assignee is not a member of the active company")
    due_at = payload.due_at
    if due_at:
        if due_at.tzinfo is None:
            raise HTTPException(status_code=422, detail="due_at must include a timezone")
        due_at = due_at.astimezone(timezone.utc).replace(tzinfo=None)
    run_id = await repo.start_run(company_id=company_id, template=template, asset_id=payload.asset_id,
                                  ticket_id=payload.ticket_id, assignee_id=payload.assignee_id,
                                  priority=payload.priority, due_at=due_at, user_id=int(user["id"]))
    await audit_service.record(action="process.run.start", request=request, user_id=int(user["id"]),
                               entity_type="process_run", entity_id=run_id,
                               after={"company_id": company_id, "template_id": payload.template_id,
                                      "asset_id": payload.asset_id, "ticket_id": payload.ticket_id})
    return await repo.get_run(company_id, run_id)


@router.get("/runs/{run_id}", summary="Get an execution run and its snapshotted steps")
async def get_run(run_id: int, context: tuple[dict, int, dict | None] = Depends(access_context)):
    run = await repo.get_run(context[1], run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Process run not found")
    return run


@router.patch("/runs/{run_id}", summary="Update execution run status")
async def update_run(run_id: int, payload: RunStatusUpdate, request: Request,
                     context: tuple[dict, int, dict | None] = Depends(access_context)):
    user, company_id = require_write(context)
    if not await repo.update_run_status(company_id, run_id, payload.status, int(user["id"])):
        raise HTTPException(status_code=404, detail="Process run not found")
    await audit_service.record(action="process.run.update", request=request, user_id=int(user["id"]),
                               entity_type="process_run", entity_id=run_id, after={"status": payload.status})
    return await repo.get_run(company_id, run_id)


@router.patch("/runs/{run_id}/steps/{step_id}", summary="Record step notes and completion attribution")
async def update_step(run_id: int, step_id: int, payload: StepUpdate, request: Request,
                      context: tuple[dict, int, dict | None] = Depends(access_context)):
    user, company_id = require_write(context)
    if not await repo.update_step(company_id, run_id, step_id, status=payload.status,
                                  notes=payload.notes, user_id=int(user["id"])):
        raise HTTPException(status_code=404, detail="Process run step not found")
    await audit_service.record(action="process.run_step.update", request=request, user_id=int(user["id"]),
                               entity_type="process_run_step", entity_id=step_id,
                               after={"run_id": run_id, "status": payload.status})
    return await repo.get_run(company_id, run_id)
