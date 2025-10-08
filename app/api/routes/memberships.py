from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import roles as role_repo
from app.repositories import users as user_repo
from app.schemas.memberships import MembershipCreate, MembershipResponse, MembershipUpdate
from app.services import audit as audit_service

router = APIRouter(prefix="/companies/{company_id}/memberships", tags=["Company Memberships"])


async def _ensure_company(company_id: int) -> dict:
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


@router.get("", response_model=list[MembershipResponse])
async def list_memberships(
    company_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    await _ensure_company(company_id)
    memberships = await membership_repo.list_company_memberships(company_id)
    return memberships


@router.post("", response_model=MembershipResponse, status_code=status.HTTP_201_CREATED)
async def create_membership(
    company_id: int,
    payload: MembershipCreate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    await _ensure_company(company_id)
    user = await user_repo.get_user_by_id(payload.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    role = await role_repo.get_role_by_id(payload.role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    existing = await membership_repo.get_membership_by_company_user(company_id, payload.user_id)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Membership already exists")
    created = await membership_repo.create_membership(
        company_id=company_id,
        user_id=payload.user_id,
        role_id=payload.role_id,
        status=payload.status,
        invited_by=current_user["id"],
    )
    await audit_service.log_action(
        action="membership.created",
        user_id=current_user["id"],
        entity_type="company_membership",
        entity_id=created["id"],
        new_value=created,
        metadata={"company_id": company_id},
        request=request,
    )
    return created


@router.patch("/{membership_id}", response_model=MembershipResponse)
async def update_membership(
    company_id: int,
    membership_id: int,
    payload: MembershipUpdate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    await _ensure_company(company_id)
    membership = await membership_repo.get_membership_by_id(membership_id)
    if not membership or membership["company_id"] != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    data = payload.model_dump(exclude_unset=True)
    if "role_id" in data:
        role = await role_repo.get_role_by_id(data["role_id"])
        if not role:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    updated = await membership_repo.update_membership(membership_id, **data)
    await audit_service.log_action(
        action="membership.updated",
        user_id=current_user["id"],
        entity_type="company_membership",
        entity_id=membership_id,
        previous_value=membership,
        new_value=updated,
        metadata={"company_id": company_id},
        request=request,
    )
    return updated


@router.delete("/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_membership(
    company_id: int,
    membership_id: int,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    await _ensure_company(company_id)
    membership = await membership_repo.get_membership_by_id(membership_id)
    if not membership or membership["company_id"] != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    await membership_repo.delete_membership(membership_id)
    await audit_service.log_action(
        action="membership.deleted",
        user_id=current_user["id"],
        entity_type="company_membership",
        entity_id=membership_id,
        previous_value=membership,
        new_value=None,
        metadata={"company_id": company_id},
        request=request,
    )
    return None
