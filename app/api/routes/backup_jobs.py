"""Backup History API.

Two surfaces:

* ``POST /api/backup-status`` — public webhook called by external scripts
  (PowerShell, n8n, native backup apps, ...). Authenticated implicitly by
  the random ``job_id`` token — no user session is required, which is
  why the endpoint is exempted from CSRF in ``app.main``.
* ``/api/backup-jobs`` — super-admin CRUD for managing jobs.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies.auth import require_super_admin
from app.repositories import companies as company_repo
from app.schemas.backup_jobs import (
    BackupJobCreate,
    BackupJobResponse,
    BackupJobUpdate,
    BackupStatusReport,
    BackupStatusReportResponse,
    serialise_job,
)
from app.services import backup_jobs as backup_jobs_service

router = APIRouter(tags=["Backup History"])


# ---------------------------------------------------------------------------
# Public webhook
# ---------------------------------------------------------------------------


@router.post(
    "/api/backup-status",
    response_model=BackupStatusReportResponse,
    summary="Report the status of a backup job",
    description=(
        "Public webhook for backup scripts. The body must contain the "
        "unique randomly-generated `job_id` issued when the job was "
        "created in the admin UI, the `status` of the run "
        "(`pass`, `fail`, `warn`, or `unknown`; aliases such as `ok` / "
        "`error` are accepted), and an optional human-readable `message`."
    ),
)
async def report_backup_status(
    request: Request,
    payload: BackupStatusReport,
) -> BackupStatusReportResponse:
    source = request.client.host if request.client else None
    try:
        result = await backup_jobs_service.record_status(
            job_token=payload.job_id,
            status=payload.status,
            message=payload.message,
            source=source,
        )
    except LookupError as exc:
        # Do not leak whether the token exists vs. is inactive.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup job not found",
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Backup job is disabled",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    job = result["job"]
    event = result["event"]
    return BackupStatusReportResponse(
        job_id=int(job["id"]),
        company_id=int(job["company_id"]),
        name=str(job.get("name") or ""),
        event_date=event["event_date"],
        status=str(event.get("status") or backup_jobs_service.DEFAULT_STATUS),
        status_message=event.get("status_message"),
        reported_at=event.get("reported_at"),
    )


# ---------------------------------------------------------------------------
# Super-admin CRUD
# ---------------------------------------------------------------------------


async def _ensure_company(company_id: int) -> None:
    company = await company_repo.get_company_by_id(int(company_id))
    if not company:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company not found",
        )


@router.get("/api/backup-jobs", response_model=list[BackupJobResponse])
async def list_backup_jobs(
    company_id: int | None = Query(None, gt=0),
    include_inactive: bool = Query(True),
    _: dict[str, Any] = Depends(require_super_admin),
) -> list[BackupJobResponse]:
    jobs = await backup_jobs_service.list_jobs_with_latest(
        company_id=company_id, include_inactive=include_inactive
    )
    return [serialise_job(job) for job in jobs]


@router.post(
    "/api/backup-jobs",
    response_model=BackupJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_backup_job(
    payload: BackupJobCreate,
    user: dict[str, Any] = Depends(require_super_admin),
) -> BackupJobResponse:
    await _ensure_company(payload.company_id)
    try:
        job = await backup_jobs_service.create_job(
            company_id=payload.company_id,
            name=payload.name,
            description=payload.description,
            is_active=payload.is_active,
            created_by=int(user.get("id")) if user.get("id") else None,
            alert_no_success_days=payload.alert_no_success_days,
            alert_fail_days=payload.alert_fail_days,
            alert_unknown_days=payload.alert_unknown_days,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return serialise_job(job)


@router.put("/api/backup-jobs/{job_id}", response_model=BackupJobResponse)
async def update_backup_job(
    job_id: int,
    payload: BackupJobUpdate,
    _: dict[str, Any] = Depends(require_super_admin),
) -> BackupJobResponse:
    if payload.company_id is not None:
        await _ensure_company(payload.company_id)
    try:
        updated = await backup_jobs_service.update_job(
            job_id,
            name=payload.name,
            description=payload.description,
            company_id=payload.company_id,
            is_active=payload.is_active,
            alert_no_success_days=payload.alert_no_success_days,
            alert_fail_days=payload.alert_fail_days,
            alert_unknown_days=payload.alert_unknown_days,
            clear_alert_no_success_days=payload.alert_no_success_days is None
            and "alert_no_success_days" in payload.model_fields_set,
            clear_alert_fail_days=payload.alert_fail_days is None
            and "alert_fail_days" in payload.model_fields_set,
            clear_alert_unknown_days=payload.alert_unknown_days is None
            and "alert_unknown_days" in payload.model_fields_set,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup job not found",
        )
    return serialise_job(updated)


@router.post(
    "/api/backup-jobs/{job_id}/regenerate-token",
    response_model=BackupJobResponse,
)
async def regenerate_backup_job_token(
    job_id: int,
    _: dict[str, Any] = Depends(require_super_admin),
) -> BackupJobResponse:
    updated = await backup_jobs_service.regenerate_token(job_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup job not found",
        )
    return serialise_job(updated)


@router.delete(
    "/api/backup-jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_backup_job(
    job_id: int,
    _: dict[str, Any] = Depends(require_super_admin),
) -> None:
    await backup_jobs_service.delete_job(job_id)
