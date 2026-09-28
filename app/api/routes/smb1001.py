"""SMB1001 compliance API."""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.repositories import smb1001 as smb1001_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import users as users_repo
from app.services import audit as audit_service

router = APIRouter(prefix="/api/smb1001", tags=["SMB1001 Compliance"])

StatusLiteral = Literal["not_started", "in_progress", "compliant", "non_compliant", "not_applicable"]


class SMB1001ControlComplianceUpdate(BaseModel):
    status: Optional[StatusLiteral] = None
    evidence: Optional[str] = None
    notes: Optional[str] = None
    owner_user_id: Optional[int] = None
    last_reviewed_date: Optional[date] = None
    target_compliance_date: Optional[date] = None


class SMB1001TargetTierUpdate(BaseModel):
    target_tier: int = Field(ge=1, le=smb1001_repo.MAX_TIER)


async def _assert_company_compliance_access(user: dict, company_id: int, *, write: bool = False) -> None:
    if user.get("is_super_admin"):
        return
    if user.get("company_id") != company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access compliance for your own company",
        )
    membership = None
    if user.get("id") is not None:
        membership = await user_company_repo.get_user_company(user["id"], company_id)
    if not membership or not membership.get("can_view_compliance"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to SMB1001 compliance for this company",
        )
    if write and not membership.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required to manage SMB1001 compliance",
        )


async def _validate_owner(company_id: int, owner_user_id: int | None) -> None:
    if owner_user_id is None:
        return
    owner = await users_repo.get_user_by_id(owner_user_id)
    if not owner or int(owner.get("company_id") or 0) != company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner must be a valid company member",
        )


@router.get("/tiers", response_model=list[dict])
async def list_tiers(
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    """List the five SMB1001 tiers (Bronze to Diamond)."""
    return await smb1001_repo.list_tiers()


@router.get("/controls", response_model=list[dict])
async def list_controls(
    tier_level: Optional[int] = None,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    """List SMB1001 controls, optionally for a single tier."""
    return await smb1001_repo.list_controls(tier_level=tier_level)


@router.get("/companies/{company_id}/overview", response_model=dict)
async def get_company_overview(
    company_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Controls with the company's status, per-tier progress and the tier achieved."""
    await _assert_company_compliance_access(user, company_id)
    await smb1001_repo.ensure_company_profile(company_id, user_id=user.get("id"))
    return await smb1001_repo.get_company_overview(company_id)


@router.patch("/companies/{company_id}/controls/{control_id}/compliance", response_model=dict)
async def update_control_compliance(
    company_id: int,
    control_id: int,
    payload: SMB1001ControlComplianceUpdate,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Create or update the company's status, evidence and notes for one control."""
    await _assert_company_compliance_access(user, company_id, write=True)
    control = await smb1001_repo.get_control(control_id)
    if not control:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Control not found")
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No changes supplied")
    await _validate_owner(company_id, updates.get("owner_user_id"))
    before = await smb1001_repo.get_company_control_compliance(company_id, control_id)
    try:
        record = await smb1001_repo.save_company_control_compliance(
            company_id,
            control_id,
            user_id=user.get("id"),
            change_summary=f"{control['code']} updated.",
            **updates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await audit_service.record(
        action="smb1001.control.update",
        request=request,
        user_id=user.get("id"),
        entity_type="smb1001_control_compliance",
        entity_id=record.get("id"),
        before=before,
        after=record,
        metadata={"company_id": company_id, "control_id": control_id, "control_code": control["code"]},
    )
    return record


@router.get("/companies/{company_id}/controls/{control_id}/audit", response_model=list[dict])
async def list_control_audit(
    company_id: int,
    control_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    await _assert_company_compliance_access(user, company_id)
    return await smb1001_repo.list_control_audit(company_id, control_id)


@router.put("/companies/{company_id}/target-tier", response_model=dict)
async def set_target_tier(
    company_id: int,
    payload: SMB1001TargetTierUpdate,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Set the SMB1001 tier the company is working towards."""
    await _assert_company_compliance_access(user, company_id, write=True)
    before = await smb1001_repo.get_profile(company_id)
    profile = await smb1001_repo.set_target_tier(company_id, payload.target_tier)
    await audit_service.record(
        action="smb1001.target_tier.update",
        request=request,
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        before={"target_tier": (before or {}).get("target_tier")},
        after={"target_tier": profile.get("target_tier")},
    )
    return profile


@router.post("/companies/{company_id}/import-essential8", response_model=dict)
async def import_essential8(
    company_id: int,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Convert Essential 8 progress into SMB1001 statuses for controls not yet started."""
    await _assert_company_compliance_access(user, company_id, write=True)
    result = await smb1001_repo.import_essential8_progress(company_id, user_id=user.get("id"))
    await audit_service.record(
        action="smb1001.essential8_import",
        request=request,
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        metadata={"updated_count": result.get("updated_count", 0)},
    )
    return result


__all__ = ["router"]
