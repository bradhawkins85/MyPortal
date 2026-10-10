"""Scheduled and onboarding RMM scripts: page and APIs.

Company scoped like the Scripts page (``menu.rmm_scripts``; write access
changes schedules and onboarding). Onboarding steps belong to the company.
Schedules for every company are shown to everyone and changed only by super
admins.

* ``GET /rmm/automation`` schedules, onboarding steps and onboarding history
* ``/api/rmm/schedules`` create, edit, enable, run now and delete schedules
* ``/api/rmm/onboarding/steps`` add, edit, reorder and remove onboarding steps
* ``/api/rmm/onboarding/runs`` start, view and cancel onboarding on a device
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_automation as automation_repo
from app.services import audit as audit_service
from app.services import rmm_automation as automation
from app.services.rmm_scripts import RunRequestError

from .routes import _assets_routes, _context, _serialise

router = APIRouter(tags=["RMM automation"])

Scope = Literal["company", "all"]


def _scope_company(scope: str, company_id: int, user: dict[str, Any]) -> int | None:
    """``None`` for an every-company item, which only super admins may change."""

    if scope == "all":
        if not user.get("is_super_admin"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only super admins can change items for every company")
        return None
    return int(company_id)


def _check_visible(item: dict[str, Any] | None, company_id: int, label: str) -> dict[str, Any]:
    if not item or (item.get("company_id") is not None and int(item["company_id"]) != int(company_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return item


def _check_writable(item: dict[str, Any], user: dict[str, Any]) -> None:
    if item.get("company_id") is None and not user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only super admins can change items for every company")


def _invalid(exc: RunRequestError) -> JSONResponse:
    return JSONResponse({"detail": "Check the highlighted fields.", "errors": exc.errors}, status_code=400)


def _schedule_public(schedule: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in schedule.items() if key != "entries_encrypted"}
    public["scope"] = "all" if schedule.get("company_id") is None else "company"
    return _serialise(public) or {}


def _step_public(step: dict[str, Any]) -> dict[str, Any]:
    return _serialise({key: value for key, value in step.items() if key != "entries_encrypted"}) or {}


async def _editable(item: dict[str, Any]) -> dict[str, str]:
    script = await rmm_repo.get_script(int(item["script_id"]))
    if not script:
        return {}
    return automation.editable_values(script, automation.decrypt_entries(item.get("entries_encrypted")))


@router.get("/rmm/automation", response_class=HTMLResponse, summary="Scheduled and onboarding scripts")
async def automation_page(request: Request):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user, company, company_id, can_run = context
    await rmm_repo.expire_stale_runs()
    schedules = await automation_repo.list_schedules(company_id=company_id)
    agents = await rmm_repo.list_company_agents(company_id)
    names = await automation.tag_names()
    return await _assets_routes()._main()._render_template(
        "rmm/automation.html",
        request,
        user,
        extra={
            "title": "Script automation",
            "company": company,
            "schedules": [_schedule_public(item) for item in schedules],
            "steps": [
                {**_step_public(step), "tag_names": [names[tag_id] for tag_id in step.get("tag_ids") or [] if tag_id in names]}
                for step in await automation_repo.list_steps(company_id=company_id)
            ],
            "onboarding_runs": [_serialise(run) for run in await automation_repo.list_onboarding_runs(company_id=company_id)],
            "agents": [_serialise(agent) for agent in agents],
            "tags": [{"id": tag_id, "name": name} for tag_id, name in names.items()],
            "can_run": can_run,
            "is_super_admin": bool(user.get("is_super_admin")),
            "default_timezone": automation.default_timezone(),
        },
    )


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #


class ScheduleRequest(BaseModel):
    name: str = Field(default="", max_length=255)
    scope: Scope = "company"
    script_id: int
    cron: str = Field(max_length=128)
    timezone: str = Field(default="", max_length=64)
    target_mode: str = Field(default="all", max_length=16)
    asset_ids: list[int] = Field(default_factory=list, max_length=automation.MAX_SCHEDULE_ASSETS)
    tag_ids: list[int] = Field(default_factory=list, max_length=automation.MAX_SCHEDULE_TAGS)
    values: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int | None = None
    enabled: bool = True


@router.get("/api/rmm/schedules/preview", summary="Next times a cron schedule runs")
async def schedule_preview(
    request: Request,
    cron: str = Query(max_length=128),
    timezone: str = Query(default="", max_length=64),
):
    await _context(request, api=True)
    try:
        expression = automation.validate_cron(cron)
        tz_name = automation.validate_timezone(timezone)
    except RunRequestError as exc:
        return _invalid(exc)
    times = automation.next_runs(expression, tz_name, count=3)
    return {"timezone": tz_name, "next_runs": [value.isoformat() for value in times]}


@router.get("/api/rmm/schedules/{schedule_id:int}", summary="Get a schedule and its saved values")
async def get_schedule_api(schedule_id: int, request: Request):
    _user, _company, company_id, _can_run = await _context(request, api=True)
    schedule = _check_visible(await automation_repo.get_schedule(schedule_id, with_secrets=True), company_id, "Schedule")
    return {"schedule": {**_schedule_public(schedule), "values": await _editable(schedule)}}


async def _save_schedule(request: Request, payload: ScheduleRequest, schedule_id: int | None):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    if schedule_id is not None:
        existing = _check_visible(await automation_repo.get_schedule(schedule_id), company_id, "Schedule")
        _check_writable(existing, user)
    scope_company = _scope_company(payload.scope, company_id, user)
    try:
        saved_id = await automation.save_schedule(
            schedule_id=schedule_id, company_id=scope_company, payload=payload.model_dump(), user_id=user.get("id"),
        )
    except RunRequestError as exc:
        return _invalid(exc)
    await audit_service.record(
        action="rmm.schedule.create" if schedule_id is None else "rmm.schedule.update",
        request=request, user_id=user.get("id"), entity_type="rmm_schedule", entity_id=saved_id,
        metadata={"name": payload.name, "script_id": payload.script_id, "cron": payload.cron, "company_id": scope_company},
    )
    return {"schedule": _schedule_public(await automation_repo.get_schedule(saved_id) or {})}


@router.post("/api/rmm/schedules", summary="Create a schedule")
async def create_schedule_api(payload: ScheduleRequest, request: Request):
    return await _save_schedule(request, payload, None)


@router.put("/api/rmm/schedules/{schedule_id:int}", summary="Update a schedule")
async def update_schedule_api(schedule_id: int, payload: ScheduleRequest, request: Request):
    return await _save_schedule(request, payload, schedule_id)


class EnabledRequest(BaseModel):
    enabled: bool


async def _writable_schedule(request: Request, schedule_id: int):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    schedule = _check_visible(await automation_repo.get_schedule(schedule_id), company_id, "Schedule")
    _check_writable(schedule, user)
    return user, schedule


@router.post("/api/rmm/schedules/{schedule_id:int}/enabled", summary="Turn a schedule on or off")
async def schedule_enabled_api(schedule_id: int, payload: EnabledRequest, request: Request):
    user, schedule = await _writable_schedule(request, schedule_id)
    await automation.set_schedule_enabled(schedule, payload.enabled)
    await audit_service.record(
        action="rmm.schedule.enable" if payload.enabled else "rmm.schedule.disable",
        request=request, user_id=user.get("id"), entity_type="rmm_schedule", entity_id=schedule_id,
    )
    return {"schedule": _schedule_public(await automation_repo.get_schedule(schedule_id) or {})}


@router.post("/api/rmm/schedules/{schedule_id:int}/run", summary="Run a schedule now")
async def schedule_run_now_api(schedule_id: int, request: Request):
    user, _schedule = await _writable_schedule(request, schedule_id)
    result = await automation.run_schedule(schedule_id, requested_by_user_id=user.get("id"))
    await audit_service.record(
        action="rmm.schedule.run", request=request, user_id=user.get("id"), entity_type="rmm_schedule",
        entity_id=schedule_id, metadata={"runs": [item["run_id"] for item in result["queued"]]},
    )
    return {"summary": result["summary"], "queued": len(result["queued"]), "problems": result["problems"]}


@router.delete("/api/rmm/schedules/{schedule_id:int}", summary="Delete a schedule")
async def delete_schedule_api(schedule_id: int, request: Request):
    user, schedule = await _writable_schedule(request, schedule_id)
    await automation_repo.delete_schedule(schedule_id)
    await audit_service.record(
        action="rmm.schedule.delete", request=request, user_id=user.get("id"), entity_type="rmm_schedule",
        entity_id=schedule_id, metadata={"name": schedule.get("name")},
    )
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Onboarding steps
# --------------------------------------------------------------------------- #


class StepRequest(BaseModel):
    script_id: int
    tag_ids: list[int] = Field(default_factory=list, max_length=automation.MAX_SCHEDULE_TAGS)
    values: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int | None = None
    continue_on_failure: bool = False


class OrderRequest(BaseModel):
    step_ids: list[int] = Field(default_factory=list, max_length=500)


@router.get("/api/rmm/onboarding/steps/{step_id:int}", summary="Get an onboarding step and its saved values")
async def get_step_api(step_id: int, request: Request):
    _user, _company, company_id, _can_run = await _context(request, api=True)
    step = _check_visible(await automation_repo.get_step(step_id, with_secrets=True), company_id, "Step")
    return {"step": {**_step_public(step), "values": await _editable(step)}}


async def _save_step(request: Request, payload: StepRequest, step_id: int | None):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    if step_id is not None:
        _check_visible(await automation_repo.get_step(step_id), company_id, "Step")
    try:
        saved_id = await automation.save_step(
            step_id=step_id, company_id=company_id, payload=payload.model_dump(), user_id=user.get("id"),
        )
    except RunRequestError as exc:
        return _invalid(exc)
    await audit_service.record(
        action="rmm.onboarding.step.create" if step_id is None else "rmm.onboarding.step.update",
        request=request, user_id=user.get("id"), entity_type="rmm_onboarding_step", entity_id=saved_id,
        metadata={"script_id": payload.script_id, "company_id": company_id, "tag_ids": payload.tag_ids},
    )
    return {"step": _step_public(await automation_repo.get_step(saved_id) or {})}


@router.post("/api/rmm/onboarding/steps", summary="Add an onboarding step")
async def create_step_api(payload: StepRequest, request: Request):
    return await _save_step(request, payload, None)


@router.put("/api/rmm/onboarding/steps/{step_id:int}", summary="Update an onboarding step")
async def update_step_api(step_id: int, payload: StepRequest, request: Request):
    return await _save_step(request, payload, step_id)


@router.delete("/api/rmm/onboarding/steps/{step_id:int}", summary="Remove an onboarding step")
async def delete_step_api(step_id: int, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    step = _check_visible(await automation_repo.get_step(step_id), company_id, "Step")
    await automation_repo.delete_step(step_id)
    await audit_service.record(
        action="rmm.onboarding.step.delete", request=request, user_id=user.get("id"),
        entity_type="rmm_onboarding_step", entity_id=step_id, metadata={"script": step.get("script_name")},
    )
    return {"ok": True}


@router.post("/api/rmm/onboarding/steps/order", summary="Reorder onboarding steps")
async def reorder_steps_api(payload: OrderRequest, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    try:
        await automation.reorder_steps(company_id, payload.step_ids)
    except RunRequestError as exc:
        return JSONResponse({"detail": next(iter(exc.errors.values()))}, status_code=409)
    await audit_service.record(
        action="rmm.onboarding.reorder", request=request, user_id=user.get("id"),
        entity_type="rmm_onboarding_step", metadata={"company_id": company_id, "order": payload.step_ids},
    )
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Onboarding runs
# --------------------------------------------------------------------------- #


class StartOnboardingRequest(BaseModel):
    agent_id: int


async def _company_onboarding(onboarding_id: int, company_id: int) -> dict[str, Any]:
    onboarding = await automation_repo.get_onboarding_run(onboarding_id)
    if not onboarding or int(onboarding["company_id"]) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Onboarding run not found")
    return onboarding


@router.post("/api/rmm/onboarding/runs", summary="Run the onboarding scripts on a device")
async def start_onboarding_api(payload: StartOnboardingRequest, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    agent = await rmm_repo.get_agent(payload.agent_id)
    if not agent or agent.get("status") != "active" or int(agent.get("company_id") or 0) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    try:
        onboarding_id = await automation.start_onboarding(agent, started_by_user_id=user.get("id"))
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    if onboarding_id is None:
        return JSONResponse({"detail": "Add onboarding steps first."}, status_code=400)
    await audit_service.record(
        action="rmm.onboarding.start", request=request, user_id=user.get("id"),
        entity_type="rmm_onboarding_run", entity_id=onboarding_id, metadata={"agent_id": payload.agent_id},
    )
    return {"onboarding": _serialise(await automation_repo.get_onboarding_run(onboarding_id))}


@router.get("/api/rmm/onboarding/runs/{onboarding_id:int}", summary="Get an onboarding run and its steps")
async def get_onboarding_api(onboarding_id: int, request: Request):
    _user, _company, company_id, _can_run = await _context(request, api=True)
    await automation.advance_onboarding(onboarding_id)
    return {"onboarding": _serialise(await _company_onboarding(onboarding_id, company_id))}


@router.post("/api/rmm/onboarding/runs/{onboarding_id:int}/cancel", summary="Stop an onboarding run")
async def cancel_onboarding_api(onboarding_id: int, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    onboarding = await _company_onboarding(onboarding_id, company_id)
    if not await automation.cancel_onboarding(onboarding):
        return JSONResponse({"detail": "This onboarding run has already finished."}, status_code=409)
    await audit_service.record(
        action="rmm.onboarding.cancel", request=request, user_id=user.get("id"),
        entity_type="rmm_onboarding_run", entity_id=onboarding_id,
    )
    return {"onboarding": _serialise(await automation_repo.get_onboarding_run(onboarding_id))}

