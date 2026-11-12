"""CPU launch dates used to estimate each asset's approximate age."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.dependencies.auth import require_super_admin
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
    source: str | None = Field(None, description="intel_ark or manual (another source)")
    source_url: str | None = Field(None, description="The intel.com page the date was read from, if recorded")
    checked_at: datetime | None = Field(None, description="When the date was last recorded")


class CpuReleaseDateList(BaseModel):
    module_enabled: bool
    processors: list[CpuReleaseDate]


class CpuReleaseDateUpdate(BaseModel):
    launch_date: str | None = Field(
        None,
        max_length=32,
        description="The launch date as Intel ARK shows it (Q3'19), or 2019-07, Jul 2019 or 2019. "
                    "Send null to clear the date.",
        examples=["Q3'19"],
    )
    source: Literal["intel_ark", "manual"] = Field("manual", description="Where the date came from")
    source_url: str | None = Field(
        None, max_length=512, description="Optional https link to the processor's page on intel.com",
    )


class RefreshResult(BaseModel):
    status: str
    processors: int | None = None
    assets_updated: int | None = None
    reason: str | None = None


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


@router.post(
    "/refresh",
    response_model=RefreshResult,
    summary="Register new processor models and recalculate asset ages now",
)
async def refresh_cpu_release_dates(
    current_user: dict = Depends(require_super_admin),
) -> RefreshResult:
    if not await cpu_service.is_module_enabled():
        raise HTTPException(status_code=409, detail="Enable the CPU age lookup module first")
    return RefreshResult(**await cpu_service.refresh())


@router.put(
    "/{entry_id}",
    response_model=CpuReleaseDate,
    summary="Record or clear a processor's launch date",
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
    release = None
    if payload.launch_date and payload.launch_date.strip():
        release = cpu_service.parse_release_text(payload.launch_date)
        if release is None:
            raise HTTPException(status_code=422, detail="Use a launch date such as Q3'19, 2019-07 or Jul 2019")
        if release[0] > date.today():
            raise HTTPException(status_code=422, detail="The launch date cannot be in the future")
    try:
        updated = await cpu_service.set_release_date(
            entry_id, release, source=payload.source, source_url=payload.source_url,
        )
    except cpu_service.LaunchDateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit_service.record(
        action="cpu_release_date.update",
        request=request,
        user_id=current_user.get("id"),
        entity_type="cpu_release_date",
        entity_id=entry_id,
        before={"release_date": existing["release_date"].isoformat() if existing.get("release_date") else None,
                "source": existing.get("source"), "source_url": existing.get("source_url")},
        after={"release_date": release[0].isoformat() if release else None,
               "source": updated.get("source"), "source_url": updated.get("source_url")},
    )
    return _entry(updated)
