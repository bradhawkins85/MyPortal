from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import require_super_admin
from app.repositories import automations as automation_repo
from app.schemas.automations import (
    AutomationCreate,
    AutomationExecutionResult,
    AutomationResponse,
    AutomationRunResponse,
    AutomationUpdate,
)
from app.services import automations as automation_service

router = APIRouter(prefix="/api/automations", tags=["Automations"])


@router.get("/", response_model=list[AutomationResponse])
async def list_automations(
    status_filter: str | None = Query(default=None, alias="status"),
    kind: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(require_super_admin),
) -> list[AutomationResponse]:
    records = await automation_repo.list_automations(
        status=status_filter,
        kind=kind,
        limit=limit,
        offset=offset,
    )
    return [AutomationResponse(**record) for record in records]


@router.post("/", response_model=AutomationResponse, status_code=status.HTTP_201_CREATED)
async def create_automation(
    payload: AutomationCreate,
    current_user: dict = Depends(require_super_admin),
) -> AutomationResponse:
    data = payload.model_dump()
    next_run = None
    if data.get("status") == "active":
        next_run = automation_service.calculate_next_run(data)
    record = await automation_repo.create_automation(
        name=data["name"],
        description=data.get("description"),
        kind=data["kind"],
        cadence=data.get("cadence"),
        cron_expression=data.get("cron_expression"),
        scheduled_time=data.get("scheduled_time"),
        run_once=data.get("run_once", False),
        trigger_event=data.get("trigger_event"),
        trigger_filters=data.get("trigger_filters"),
        action_module=data.get("action_module"),
        action_payload=data.get("action_payload"),
        status=data.get("status", "inactive"),
        next_run_at=next_run,
    )
    if record and record.get("status") == "active":
        refreshed = await automation_service.refresh_schedule(int(record["id"]))
        if refreshed:
            record = refreshed
    return AutomationResponse(**record)


@router.get("/{automation_id}", response_model=AutomationResponse)
async def get_automation(automation_id: int, current_user: dict = Depends(require_super_admin)) -> AutomationResponse:
    record = await automation_repo.get_automation(automation_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")
    return AutomationResponse(**record)


@router.put("/{automation_id}", response_model=AutomationResponse)
async def update_automation(
    automation_id: int,
    payload: AutomationUpdate,
    current_user: dict = Depends(require_super_admin),
) -> AutomationResponse:
    existing = await automation_repo.get_automation(automation_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")
    data = payload.model_dump(exclude_unset=True)
    if data:
        await automation_repo.update_automation(automation_id, **data)
    refreshed = await automation_service.refresh_schedule(automation_id)
    record = refreshed or await automation_repo.get_automation(automation_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")
    return AutomationResponse(**record)


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation(automation_id: int, current_user: dict = Depends(require_super_admin)) -> None:
    existing = await automation_repo.get_automation(automation_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")
    await automation_repo.delete_automation(automation_id)


@router.get("/{automation_id}/runs", response_model=list[AutomationRunResponse])
async def list_runs(automation_id: int, current_user: dict = Depends(require_super_admin)) -> list[AutomationRunResponse]:
    existing = await automation_repo.get_automation(automation_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")
    runs = await automation_repo.list_runs(automation_id)
    return [AutomationRunResponse(**run) for run in runs]


@router.post("/{automation_id}/execute", response_model=AutomationExecutionResult)
async def execute_automation_now(
    automation_id: int,
    current_user: dict = Depends(require_super_admin),
) -> AutomationExecutionResult:
    try:
        result = await automation_service.execute_now(automation_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AutomationExecutionResult(**result)

