from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.core.logging import log_info
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.repositories import webhook_events as webhook_events_repo
from app.schemas.scheduler import (
    ActivateTaskRequest,
    RunTaskResponse,
    ScheduledTaskCreate,
    ScheduledTaskResponse,
    ScheduledTaskRunResponse,
    ScheduledTaskUpdate,
    WebhookEventAttemptResponse,
    WebhookEventResponse,
)
from app.services import webhook_monitor
from app.services.scheduler import scheduler_service

router = APIRouter(prefix="/scheduler", tags=["Scheduler"])


@router.get("/tasks", response_model=list[ScheduledTaskResponse])
async def list_tasks(
    include_inactive: bool = True,
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> list[ScheduledTaskResponse]:
    records = await scheduled_tasks_repo.list_tasks(include_inactive=include_inactive)
    return [ScheduledTaskResponse.model_validate(record) for record in records]


@router.post("/tasks", response_model=ScheduledTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: ScheduledTaskCreate,
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> ScheduledTaskResponse:
    data = payload.model_dump(by_alias=False)
    raw_max_retries = data.get("max_retries")
    raw_retry_backoff = data.get("retry_backoff_seconds")
    created = await scheduled_tasks_repo.create_task(
        name=data["name"],
        command=data["command"],
        cron=data["cron"],
        company_id=data.get("company_id"),
        description=data.get("description"),
        active=data.get("active", True),
        max_retries=int(raw_max_retries if raw_max_retries is not None else 12),
        retry_backoff_seconds=int(
            raw_retry_backoff if raw_retry_backoff is not None else 300
        ),
    )
    await scheduler_service.refresh()
    return ScheduledTaskResponse.model_validate(created)


@router.get("/tasks/{task_id}", response_model=ScheduledTaskResponse)
async def get_task(
    task_id: int,
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> ScheduledTaskResponse:
    task = await scheduled_tasks_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return ScheduledTaskResponse.model_validate(task)


@router.put("/tasks/{task_id}", response_model=ScheduledTaskResponse)
async def update_task(
    task_id: int,
    payload: ScheduledTaskUpdate,
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> ScheduledTaskResponse:
    existing = await scheduled_tasks_repo.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    updates = payload.model_dump(exclude_unset=True, by_alias=False)
    merged = existing | {
        key: value
        for key, value in updates.items()
        if value is not None
    }
    raw_max_retries = merged.get("max_retries")
    raw_retry_backoff = merged.get("retry_backoff_seconds")
    updated = await scheduled_tasks_repo.update_task(
        task_id,
        name=merged["name"],
        command=merged["command"],
        cron=merged["cron"],
        company_id=merged.get("company_id"),
        description=merged.get("description"),
        active=bool(merged.get("active", True)),
        max_retries=int(raw_max_retries if raw_max_retries is not None else 12),
        retry_backoff_seconds=int(
            raw_retry_backoff if raw_retry_backoff is not None else 300
        ),
    )
    await scheduler_service.refresh()
    return ScheduledTaskResponse.model_validate(updated)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> None:
    existing = await scheduled_tasks_repo.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    await scheduled_tasks_repo.delete_task(task_id)
    await scheduler_service.refresh()
    return None


@router.post("/tasks/{task_id}/activate", response_model=ScheduledTaskResponse)
async def activate_task(
    task_id: int,
    payload: ActivateTaskRequest,
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> ScheduledTaskResponse:
    existing = await scheduled_tasks_repo.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    updated = await scheduled_tasks_repo.set_task_active(task_id, payload.active)
    await scheduler_service.refresh()
    return ScheduledTaskResponse.model_validate(updated)


@router.post("/tasks/{task_id}/run", response_model=RunTaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_task_now(
    task_id: int,
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> RunTaskResponse:
    existing = await scheduled_tasks_repo.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    await scheduler_service.run_now(task_id)
    return RunTaskResponse()


@router.get("/tasks/{task_id}/runs", response_model=list[ScheduledTaskRunResponse])
async def list_task_runs(
    task_id: int,
    limit: int = Query(default=20, ge=1, le=200),
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> list[ScheduledTaskRunResponse]:
    existing = await scheduled_tasks_repo.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    runs = await scheduled_tasks_repo.list_recent_runs(task_ids=[task_id], limit=limit)
    return [ScheduledTaskRunResponse.model_validate(run) for run in runs]


@router.get("/webhooks", response_model=list[WebhookEventResponse])
async def list_webhook_events(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> list[WebhookEventResponse]:
    events = await webhook_events_repo.list_events(status=status_filter, limit=limit)
    return [WebhookEventResponse.model_validate(event) for event in events]


@router.get("/webhooks/{event_id}/attempts", response_model=list[WebhookEventAttemptResponse])
async def list_webhook_attempts(
    event_id: int,
    limit: int = Query(default=20, ge=1, le=200),
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> list[WebhookEventAttemptResponse]:
    event = await webhook_events_repo.get_event(event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    attempts = await webhook_events_repo.list_attempts(event_id, limit=limit)
    return [WebhookEventAttemptResponse.model_validate(attempt) for attempt in attempts]


@router.post("/webhooks/{event_id}/retry", response_model=WebhookEventResponse)
async def retry_webhook_event(
    event_id: int,
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> WebhookEventResponse:
    event = await webhook_monitor.force_retry(event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return WebhookEventResponse.model_validate(event)


@router.delete("/webhooks/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook_event(
    event_id: int,
    _: None = Depends(require_database),
    __: dict[str, Any] = Depends(require_super_admin),
) -> None:
    event = await webhook_events_repo.get_event(event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if str(event.get("status") or "").lower() == "in_progress":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Webhook is currently being delivered and cannot be deleted",
        )
    await webhook_events_repo.delete_event(event_id)
    log_info("Webhook event deleted", event_id=event_id)
    return None
