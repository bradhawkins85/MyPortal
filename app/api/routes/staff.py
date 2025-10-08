from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import staff as staff_repo
from app.schemas.staff import StaffCreate, StaffResponse, StaffUpdate


router = APIRouter(prefix="/api/staff", tags=["Staff"])


@router.get("", response_model=list[StaffResponse])
async def list_staff(
    account_action: str | None = Query(default=None, alias="accountAction"),
    email: str | None = None,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    records = await staff_repo.list_all_staff(
        account_action=account_action,
        email=email,
    )
    return [StaffResponse.model_validate(record) for record in records]


@router.post("", response_model=StaffResponse, status_code=status.HTTP_201_CREATED)
async def create_staff(
    payload: StaffCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    created = await staff_repo.create_staff(**payload.model_dump(by_alias=False))
    return StaffResponse.model_validate(created)


@router.get("/{staff_id}", response_model=StaffResponse)
async def get_staff(
    staff_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    return StaffResponse.model_validate(staff)


@router.put("/{staff_id}", response_model=StaffResponse)
async def update_staff(
    staff_id: int,
    payload: StaffUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    existing = await staff_repo.get_staff_by_id(staff_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    data = existing | payload.model_dump(exclude_unset=True, by_alias=False)
    updated = await staff_repo.update_staff(
        staff_id,
        company_id=data["company_id"],
        first_name=data["first_name"],
        last_name=data["last_name"],
        email=data["email"],
        mobile_phone=data.get("mobile_phone"),
        date_onboarded=data.get("date_onboarded"),
        date_offboarded=data.get("date_offboarded"),
        enabled=bool(data.get("enabled", True)),
        street=data.get("street"),
        city=data.get("city"),
        state=data.get("state"),
        postcode=data.get("postcode"),
        country=data.get("country"),
        department=data.get("department"),
        job_title=data.get("job_title"),
        org_company=data.get("org_company"),
        manager_name=data.get("manager_name"),
        account_action=data.get("account_action"),
        syncro_contact_id=data.get("syncro_contact_id"),
    )
    return StaffResponse.model_validate(updated)


@router.delete("/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff(
    staff_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    existing = await staff_repo.get_staff_by_id(staff_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    await staff_repo.delete_staff(staff_id)
    return None
