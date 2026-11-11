"""CPU launch dates used to estimate each asset's approximate age."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.dependencies.auth import require_super_admin
from app.core.logging import log_error
from app.repositories import cpu_release_dates as cpu_repo
from app.services import audit as audit_service
from app.services import cpu_release_dates as cpu_service

router = APIRouter(prefix="/api/cpu-release-dates", tags=["CPU Release Dates"])


class CpuReleaseDate(BaseModel):
    id: int
    cpu_key: str
    cpu_name: str
    vendor: str | None = None
    model: str | None = None
    release_date: date | None = None
    release_label: str | None = None
    source: str | None = Field(None, description="intel_ark or manual")
    source_url: str | None = None
    lookup_status: str | None = Field(None, description="found, not_found, unsupported or error")
    lookup_message: str | None = None
    checked_at: datetime | None = None


class CpuReleaseDateList(BaseModel):
    module_enabled: bool
    processors: list[CpuReleaseDate]


class CpuReleaseDateUpdate(BaseModel):
    release_date: date | None = Field(
        None,
        description="The processor's launch date. Send null to clear a manual date so the next run looks it up again.",
    )


class RefreshAccepted(BaseModel):
    status: str
    message: str


def _entry(row: dict[str, Any]) -> CpuReleaseDate:
    return CpuReleaseDate(**{key: row.get(key) for key in CpuReleaseDate.model_fields})


@router.get("", response_model=CpuReleaseDateList, summary="List processor models and their launch dates")
async def list_cpu_release_dates(
    current_user: dict = Depends(require_super_admin),
) -> CpuReleaseDateList:
    rows = await cpu_repo.list_all()
    return CpuReleaseDateList(
        module_enabled=await cpu_service.is_module_enabled(),
        processors=[_entry(row) for row in rows],
    )


async def _run_refresh() -> None:
    try:
        await cpu_service.refresh()
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("CPU release date lookup failed", error=str(exc))


@router.post(
    "/refresh",
    response_model=RefreshAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Look up missing CPU launch dates and refresh asset ages now",
)
async def refresh_cpu_release_dates(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_super_admin),
) -> RefreshAccepted:
    if not await cpu_service.is_module_enabled():
        raise HTTPException(status_code=409, detail="Enable the CPU age lookup module first")
    background_tasks.add_task(_run_refresh)
    return RefreshAccepted(
        status="queued",
        message="Processors without a launch date are being looked up; asset ages update when it finishes.",
    )


@router.put(
    "/{entry_id}",
    response_model=CpuReleaseDate,
    summary="Record or clear a processor's launch date by hand",
)
async def update_cpu_release_date(
    entry_id: int,
    payload: CpuReleaseDateUpdate,
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> CpuReleaseDate:
    existing = await cpu_repo.get(entry_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Processor not found")
    if payload.release_date and payload.release_date > date.today():
        raise HTTPException(status_code=422, detail="The launch date cannot be in the future")
    updated = await cpu_service.set_manual_release_date(entry_id, payload.release_date)
    await audit_service.record(
        action="cpu_release_date.update",
        request=request,
        user_id=current_user.get("id"),
        entity_type="cpu_release_date",
        entity_id=entry_id,
        before={"release_date": existing["release_date"].isoformat() if existing.get("release_date") else None,
                "source": existing.get("source")},
        after={"release_date": payload.release_date.isoformat() if payload.release_date else None,
               "source": "manual" if payload.release_date else None},
    )
    return _entry(updated)
