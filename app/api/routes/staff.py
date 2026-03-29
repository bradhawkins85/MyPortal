from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import staff as staff_repo
from app.repositories import staff_custom_fields as staff_custom_fields_repo
from app.repositories import staff_onboarding_workflows as staff_workflow_repo
from app.schemas.staff import StaffCreate, StaffRequestCreate, StaffResponse, StaffUpdate
from app.services import staff_onboarding_workflows as staff_onboarding_workflow_service


router = APIRouter(prefix="/api/staff", tags=["Staff"])
STAFF_REQUEST_PERMISSION = "staff.request"


async def _ensure_company_exists(company_id: int) -> None:
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")


async def _require_staff_request_access(current_user: dict, company_id: int) -> None:
    if current_user.get("is_super_admin"):
        return
    user_id = current_user.get("id")
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to request staff onboarding",
        ) from exc
    membership = await membership_repo.get_membership_by_company_user(company_id, user_id_int)
    if not membership or str(membership.get("status", "")).lower() != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Company membership required")
    permissions = set(membership.get("combined_permissions") or membership.get("permissions") or [])
    if "company.admin" not in permissions and STAFF_REQUEST_PERMISSION not in permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to request staff onboarding",
        )


@router.get("", response_model=list[StaffResponse])
async def list_staff(
    company_id: int | None = Query(default=None, alias="companyId"),
    account_action: str | None = Query(default=None, alias="accountAction"),
    email: str | None = None,
    onboarding_complete: bool | None = Query(default=None, alias="onboardingComplete"),
    onboarding_status: str | None = Query(default=None, alias="onboardingStatus"),
    created_after: datetime | None = Query(default=None, alias="createdAfter"),
    updated_after: datetime | None = Query(default=None, alias="updatedAfter"),
    cursor: str | None = Query(default=None, alias="cursor"),
    page_size: int | None = Query(default=200, alias="pageSize", ge=1, le=500),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    # If company_id is provided, only helpdesk technicians and super admins can access
    if company_id is not None:
        is_super_admin = current_user.get("is_super_admin", False)
        if not is_super_admin:
            user_id = current_user.get("id")
            try:
                user_id_int = int(user_id)
                has_helpdesk = await membership_repo.user_has_permission(
                    user_id_int, "helpdesk.technician"
                )
                if not has_helpdesk:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Insufficient permissions to list staff"
                    )
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Insufficient permissions to list staff"
                )
        records = await staff_repo.list_staff(
            company_id,
            enabled=True,
            onboarding_complete=onboarding_complete,
            onboarding_status=onboarding_status,
            created_after=created_after,
            updated_after=updated_after,
            cursor=cursor,
            page_size=page_size,
        )
    else:
        # Listing all staff requires super admin
        if not current_user.get("is_super_admin", False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions to list all staff"
            )
        records = await staff_repo.list_all_staff(
            account_action=account_action,
            email=email,
        )
    workflow_map = await staff_workflow_repo.list_executions_for_staff_ids(
        [int(record["id"]) for record in records if record.get("id") is not None]
    )
    for record in records:
        execution = workflow_map.get(int(record["id"])) if record.get("id") is not None else None
        record["workflow_status"] = execution
    return [StaffResponse.model_validate(record) for record in records]


@router.post("", response_model=StaffResponse, status_code=status.HTTP_201_CREATED)
async def create_staff(
    payload: StaffCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    payload_data = payload.model_dump(by_alias=False)
    custom_fields = payload_data.pop("custom_fields", None) or {}
    payload_data.setdefault("onboarding_status", "requested")
    payload_data.setdefault("onboarding_complete", False)
    payload_data.setdefault("onboarding_completed_at", None)
    payload_data.setdefault("approval_status", "approved")
    payload_data.setdefault("approved_by_user_id", int(__.get("id")) if __.get("id") is not None else None)
    payload_data.setdefault("approved_at", datetime.now(tz=timezone.utc))
    created = await staff_repo.create_staff(**payload_data)
    await staff_custom_fields_repo.set_staff_field_values_by_name(
        company_id=created["company_id"],
        staff_id=created["id"],
        values=custom_fields,
    )
    created = await staff_repo.get_staff_by_id(created["id"]) or created
    await staff_onboarding_workflow_service.enqueue_staff_onboarding_workflow(
        company_id=int(created["company_id"]),
        staff_id=int(created["id"]),
        initiated_by_user_id=int(__.get("id")) if __.get("id") is not None else None,
    )
    created["workflow_status"] = await staff_onboarding_workflow_service.get_staff_workflow_status(int(created["id"]))
    return StaffResponse.model_validate(created)


@router.post("/requests", response_model=StaffResponse, status_code=status.HTTP_201_CREATED)
async def create_staff_request(
    payload: StaffRequestCreate,
    company_id: int = Query(..., alias="companyId"),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_company_exists(company_id)
    await _require_staff_request_access(current_user, company_id)
    payload_data = payload.model_dump(by_alias=False)
    payload_data.pop("company_id", None)
    custom_fields = payload_data.pop("custom_fields", None) or {}
    payload_data["company_id"] = company_id
    payload_data.setdefault("onboarding_status", "requested")
    payload_data.setdefault("onboarding_complete", False)
    payload_data.setdefault("onboarding_completed_at", None)
    payload_data.setdefault("approval_status", "pending")
    payload_data.setdefault("requested_by_user_id", int(current_user.get("id")) if current_user.get("id") is not None else None)
    payload_data.setdefault("requested_at", datetime.now(tz=timezone.utc))
    payload_data.setdefault("approved_by_user_id", None)
    payload_data.setdefault("approved_at", None)
    payload_data.setdefault("approval_notes", None)
    created = await staff_repo.create_staff(**payload_data)
    await staff_custom_fields_repo.set_staff_field_values_by_name(
        company_id=created["company_id"],
        staff_id=created["id"],
        values=custom_fields,
    )
    created = await staff_repo.get_staff_by_id(created["id"]) or created
    created["workflow_status"] = await staff_onboarding_workflow_service.get_staff_workflow_status(int(created["id"]))
    return StaffResponse.model_validate(created)


@router.post("/{staff_id}/approve", response_model=StaffResponse)
async def approve_staff_request(
    staff_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    updated = await staff_repo.update_staff(
        staff_id,
        company_id=staff["company_id"],
        first_name=staff["first_name"],
        last_name=staff["last_name"],
        email=staff["email"],
        mobile_phone=staff.get("mobile_phone"),
        date_onboarded=staff.get("date_onboarded"),
        date_offboarded=staff.get("date_offboarded"),
        enabled=bool(staff.get("enabled", True)),
        is_ex_staff=bool(staff.get("is_ex_staff", False)),
        street=staff.get("street"),
        city=staff.get("city"),
        state=staff.get("state"),
        postcode=staff.get("postcode"),
        country=staff.get("country"),
        department=staff.get("department"),
        job_title=staff.get("job_title"),
        org_company=staff.get("org_company"),
        manager_name=staff.get("manager_name"),
        account_action=staff.get("account_action"),
        syncro_contact_id=staff.get("syncro_contact_id"),
        onboarding_status=staff.get("onboarding_status") or "requested",
        onboarding_complete=bool(staff.get("onboarding_complete", False)),
        onboarding_completed_at=staff.get("onboarding_completed_at"),
        approval_status="approved",
        approved_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        approved_at=datetime.now(tz=timezone.utc),
    )
    await staff_onboarding_workflow_service.enqueue_staff_onboarding_workflow(
        company_id=int(updated["company_id"]),
        staff_id=staff_id,
        initiated_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
    )
    updated["workflow_status"] = await staff_onboarding_workflow_service.get_staff_workflow_status(staff_id)
    return StaffResponse.model_validate(updated)


@router.get("/{staff_id}", response_model=StaffResponse)
async def get_staff(
    staff_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    staff["workflow_status"] = await staff_onboarding_workflow_service.get_staff_workflow_status(staff_id)
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
        is_ex_staff=bool(data.get("is_ex_staff", False)),
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
        onboarding_status=data.get("onboarding_status"),
        onboarding_complete=data.get("onboarding_complete"),
        onboarding_completed_at=data.get("onboarding_completed_at"),
        approval_status=data.get("approval_status"),
        requested_by_user_id=data.get("requested_by_user_id"),
        requested_at=data.get("requested_at"),
        approved_by_user_id=data.get("approved_by_user_id"),
        approved_at=data.get("approved_at"),
        request_notes=data.get("request_notes"),
        approval_notes=data.get("approval_notes"),
    )
    custom_fields = data.get("custom_fields")
    if isinstance(custom_fields, dict):
        await staff_custom_fields_repo.set_staff_field_values_by_name(
            company_id=updated["company_id"],
            staff_id=staff_id,
            values=custom_fields,
        )
        updated = await staff_repo.get_staff_by_id(staff_id) or updated

    status_value = str((data.get("onboarding_status") or "")).strip().lower()
    if status_value in {"requested", "provisioning"}:
        await staff_onboarding_workflow_service.enqueue_staff_onboarding_workflow(
            company_id=int(updated["company_id"]),
            staff_id=staff_id,
            initiated_by_user_id=int(__.get("id")) if __.get("id") is not None else None,
        )

    updated["workflow_status"] = await staff_onboarding_workflow_service.get_staff_workflow_status(staff_id)
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
