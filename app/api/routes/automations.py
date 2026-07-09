from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from app.api.dependencies.auth import require_super_admin
from app.repositories import automations as automation_repo
from app.schemas.automations import (
    AutomationCreate,
    AutomationExecutionResult,
    AutomationResponse,
    AutomationRunResponse,
    AutomationTicketPreviewResponse,
    AutomationUpdate,
)
from app.services import audit as audit_service
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
    request: Request,
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
        execution_order=data.get("execution_order", 0),
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
    await audit_service.record(
        action="automation.create",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="automation",
        entity_id=int(record["id"]) if record.get("id") is not None else None,
        before=None,
        after=record,
    )
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
    request: Request,
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
    audit_action = "automation.update"
    if "status" in data:
        new_status = (data.get("status") or "").lower()
        if new_status == "active" and (existing.get("status") or "").lower() != "active":
            audit_action = "automation.enable"
        elif new_status in {"inactive", "paused"} and (existing.get("status") or "").lower() == "active":
            audit_action = "automation.disable"
    await audit_service.record(
        action=audit_action,
        request=request,
        user_id=int(current_user["id"]),
        entity_type="automation",
        entity_id=automation_id,
        before=existing,
        after=record,
    )
    return AutomationResponse(**record)


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation(
    automation_id: int,
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> None:
    existing = await automation_repo.get_automation(automation_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")
    await automation_repo.delete_automation(automation_id)
    await audit_service.record(
        action="automation.delete",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="automation",
        entity_id=automation_id,
        before=existing,
        after=None,
    )


@router.get("/{automation_id}/preview", response_model=AutomationTicketPreviewResponse)
async def preview_scheduled_ticket_automation(
    automation_id: int,
    limit: int = Query(default=1000, ge=1, le=5000),
    current_user: dict = Depends(require_super_admin),
) -> AutomationTicketPreviewResponse:
    try:
        result = await automation_service.preview_scheduled_ticket_automation_by_id(automation_id, limit=limit)
    except ValueError as exc:
        message = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST if "Only scheduled" in message else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=message) from exc
    return AutomationTicketPreviewResponse(**result)


@router.get("/{automation_id}/simulate", response_model=AutomationTicketPreviewResponse)
async def simulate_event_ticket_automation(
    automation_id: int,
    limit: int = Query(default=1000, ge=1, le=5000),
    current_user: dict = Depends(require_super_admin),
) -> AutomationTicketPreviewResponse:
    try:
        result = await automation_service.simulate_event_ticket_automation_by_id(automation_id, limit=limit)
    except ValueError as exc:
        message = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST if "Only" in message else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=message) from exc
    return AutomationTicketPreviewResponse(**result)


@router.post("/{automation_id}/simulate/process", response_model=dict[str, object])
async def process_event_ticket_simulation(
    automation_id: int,
    ticket_ids: list[int] = Body(default_factory=list),
    limit: int = Query(default=1000, ge=1, le=5000),
    current_user: dict = Depends(require_super_admin),
) -> dict[str, object]:
    try:
        result = await automation_service.process_event_ticket_simulation_by_id(
            automation_id,
            ticket_ids=ticket_ids,
            limit=limit,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST if "Only" in message else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=message) from exc
    return result


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

