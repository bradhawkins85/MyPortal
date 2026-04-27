from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import licenses as license_repo
from app.services import staff_onboarding_workflows as staff_workflow_service
from app.schemas.licenses import (
    LicenseCreate,
    LicenseResponse,
    LicenseStaffResponse,
    LicenseUpdate,
)


def _to_json_safe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert datetime fields in history records to ISO-format strings."""
    result = []
    for row in records:
        safe: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, datetime):
                target = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
                safe[key] = target.astimezone(timezone.utc).isoformat()
            else:
                safe[key] = value
        result.append(safe)
    return result


router = APIRouter(prefix="/api/licenses", tags=["Licenses"])


@router.get("", response_model=list[LicenseResponse])
async def list_licenses(
    company_id: int | None = Query(default=None, alias="companyId"),
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    if company_id is not None:
        records = await license_repo.list_company_licenses(company_id)
    else:
        records = await license_repo.list_all_licenses()
    return [LicenseResponse.model_validate(record) for record in records]


@router.post("", response_model=LicenseResponse, status_code=status.HTTP_201_CREATED)
async def create_license(
    payload: LicenseCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    created = await license_repo.create_license(**payload.model_dump())
    await staff_workflow_service.process_paused_license_executions(company_id=int(created["company_id"]))
    await license_repo.record_usage_if_changed(
        license_id=int(created["id"]),
        count=int(created["count"]),
        allocated=int(created.get("allocated") or 0),
    )
    return LicenseResponse.model_validate(created)


@router.get("/{license_id}", response_model=LicenseResponse)
async def get_license(
    license_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    record = await license_repo.get_license_by_id(license_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found")
    return LicenseResponse.model_validate(record)


@router.put("/{license_id}", response_model=LicenseResponse)
async def update_license(
    license_id: int,
    payload: LicenseUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    existing = await license_repo.get_license_by_id(license_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found")
    data = existing | payload.model_dump(exclude_unset=True)
    updated = await license_repo.update_license(
        license_id,
        company_id=int(data["company_id"]),
        name=data["name"],
        platform=data["platform"],
        count=int(data["count"]),
        expiry_date=data.get("expiry_date"),
        contract_term=data.get("contract_term"),
        auto_renew=data.get("auto_renew"),
    )
    await staff_workflow_service.process_paused_license_executions(company_id=int(updated["company_id"]))
    await license_repo.record_usage_if_changed(
        license_id=license_id,
        count=int(updated["count"]),
        allocated=int(updated.get("allocated") or 0),
    )
    return LicenseResponse.model_validate(updated)


@router.delete("/{license_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_license(
    license_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    existing = await license_repo.get_license_by_id(license_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found")
    await license_repo.delete_license(license_id)
    await staff_workflow_service.process_paused_license_executions(company_id=int(existing["company_id"]))
    return None


@router.get("/{license_id}/staff", response_model=list[LicenseStaffResponse])
async def list_license_staff(
    license_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    existing = await license_repo.get_license_by_id(license_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found")
    members = await license_repo.list_staff_for_license(license_id)
    return [LicenseStaffResponse.model_validate(member) for member in members]


@router.post("/{license_id}/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def link_staff(
    license_id: int,
    staff_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    existing = await license_repo.get_license_by_id(license_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found")
    await license_repo.link_staff_to_license(staff_id, license_id)
    await staff_workflow_service.process_paused_license_executions(company_id=int(existing["company_id"]))
    refreshed = await license_repo.get_license_by_id(license_id)
    if refreshed:
        await license_repo.record_usage_if_changed(
            license_id=license_id,
            count=int(refreshed["count"]),
            allocated=int(refreshed.get("allocated") or 0),
        )
    return None


@router.delete("/{license_id}/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_staff(
    license_id: int,
    staff_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    existing = await license_repo.get_license_by_id(license_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found")
    await license_repo.unlink_staff_from_license(staff_id, license_id)
    await staff_workflow_service.process_paused_license_executions(company_id=int(existing["company_id"]))
    refreshed = await license_repo.get_license_by_id(license_id)
    if refreshed:
        await license_repo.record_usage_if_changed(
            license_id=license_id,
            count=int(refreshed["count"]),
            allocated=int(refreshed.get("allocated") or 0),
        )
    return None


@router.get("/{license_id}/usage-history", response_class=JSONResponse)
async def get_license_usage_history(
    license_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    """Return the usage history (count + allocated over time) for a license."""
    existing = await license_repo.get_license_by_id(license_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found")

    history = await license_repo.get_usage_history(license_id)

    # Seed an initial snapshot if no history has been recorded yet
    if not history:
        await license_repo.record_usage_if_changed(
            license_id=license_id,
            count=int(existing["count"]),
            allocated=int(existing.get("allocated") or 0),
        )
        history = await license_repo.get_usage_history(license_id)

    return JSONResponse(content=_to_json_safe(history))
