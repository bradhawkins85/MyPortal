from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import audit_logs as audit_repo
from app.schemas.audit_logs import AuditLogResponse

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_logs(
    entity_type: str | None = Query(default=None, max_length=100),
    entity_id: int | None = Query(default=None, ge=1),
    user_id: int | None = Query(default=None, ge=1),
    action: str | None = Query(default=None, max_length=255),
    request_id: str | None = Query(default=None, max_length=128),
    ip_address: str | None = Query(default=None, max_length=64),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    search: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    logs = await audit_repo.list_audit_logs(
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        action=action,
        request_id=request_id,
        ip_address=ip_address,
        since=since,
        until=until,
        search=search,
        limit=limit,
        offset=offset,
    )
    return logs


@router.get("/{log_id}", response_model=AuditLogResponse)
async def get_audit_log(
    log_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    log = await audit_repo.get_audit_log(log_id)
    if not log:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit log not found")
    return log
