from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

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
    limit: int = Query(default=200, ge=1, le=500),
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    logs = await audit_repo.list_audit_logs(
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        limit=limit,
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
