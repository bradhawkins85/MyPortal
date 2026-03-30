from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.api_keys import require_api_key
from app.api.dependencies.database import require_database
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import staff as staff_repo
from app.repositories import staff_custom_fields as staff_custom_fields_repo
from app.repositories import staff_onboarding_workflows as staff_workflow_repo
from app.schemas.staff import (
    StaffApprovalDecision,
    StaffCreate,
    StaffExternalCheckpointCallback,
    StaffExternalCheckpointResponse,
    StaffWorkflowManualActionRequest,
    StaffWorkflowManualActionResponse,
    StaffRequestCreate,
    StaffResponse,
    StaffUpdate,
)
from app.services import audit as audit_service
from app.services import staff_onboarding_workflows as staff_onboarding_workflow_service


router = APIRouter(prefix="/api/staff", tags=["Staff"])
STAFF_REQUEST_PERMISSION = "staff.request"
STAFF_APPROVE_PERMISSION = "staff.approve"


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


async def _has_company_admin_access(current_user: dict, company_id: int) -> bool:
    if current_user.get("is_super_admin"):
        return True
    user_id = current_user.get("id")
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return False
    membership = await membership_repo.get_membership_by_company_user(company_id, user_id_int)
    if not membership or str(membership.get("status", "")).lower() != "active":
        return False
    permissions = set(membership.get("combined_permissions") or membership.get("permissions") or [])
    return "company.admin" in permissions


async def _require_staff_approval_access(current_user: dict, company_id: int) -> None:
    if current_user.get("is_super_admin"):
        return
    user_id = current_user.get("id")
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to approve staff onboarding requests",
        ) from exc
    membership = await membership_repo.get_membership_by_company_user(company_id, user_id_int)
    if not membership or str(membership.get("status", "")).lower() != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Company membership required")
    permissions = set(membership.get("combined_permissions") or membership.get("permissions") or [])
    if "company.admin" not in permissions and STAFF_APPROVE_PERMISSION not in permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to approve staff onboarding requests",
        )


def _external_confirmation_fingerprint(
    *,
    path_staff_id: int,
    api_key_id: int,
    payload: StaffExternalCheckpointCallback,
) -> str:
    digest_payload = {
        "path_staff_id": int(path_staff_id),
        "body_staff_id": int(payload.staff_id),
        "company_id": int(payload.company_id),
        "confirmation_token": payload.confirmation_token,
        "source": payload.source,
        "callback_timestamp": payload.callback_timestamp.isoformat() if payload.callback_timestamp else None,
        "proof_reference_id": payload.proof_reference_id,
        "payload_hash": payload.payload_hash,
        "callback_payload": payload.callback_payload or {},
        "api_key_id": int(api_key_id),
    }
    encoded = json.dumps(digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _encode_staff_cursor(updated_at: datetime | str | None, staff_id: int | None) -> str | None:
    if updated_at is None or staff_id is None:
        return None
    if isinstance(updated_at, datetime):
        timestamp = updated_at.isoformat()
    else:
        timestamp = str(updated_at).strip()
    if not timestamp:
        return None
    return f"{timestamp}|{int(staff_id)}"


def _execution_action_replay_key(staff_id: int, execution_id: int, action_name: str, idempotency_key: str | None) -> str:
    safe_key = (idempotency_key or "").strip().lower()
    return f"{staff_id}:{execution_id}:{action_name}:{safe_key}"


@router.get("", response_model=list[StaffResponse])
async def list_staff(
    response: Response,
    company_id: int | None = Query(default=None, alias="companyId"),
    account_action: str | None = Query(default=None, alias="accountAction"),
    email: str | None = None,
    onboarding_complete: bool | None = Query(default=None, alias="onboardingComplete"),
    onboarding_status: str | None = Query(default=None, alias="onboardingStatus"),
    offboarding_complete: bool | None = Query(default=None, alias="offboardingComplete"),
    offboarding_status: str | None = Query(default=None, alias="offboardingStatus"),
    created_after: datetime | None = Query(default=None, alias="createdAfter"),
    updated_after: datetime | None = Query(default=None, alias="updatedAfter"),
    offboarding_requested_after: datetime | None = Query(default=None, alias="offboardingRequestedAfter"),
    offboarding_updated_after: datetime | None = Query(default=None, alias="offboardingUpdatedAfter"),
    scheduled_from: datetime | None = Query(default=None),
    scheduled_to: datetime | None = Query(default=None),
    due_only: bool = Query(default=False),
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
        safe_page_size = max(1, min(int(page_size or 200), 500))
        records = await staff_repo.list_staff(
            company_id,
            enabled=True,
            onboarding_complete=onboarding_complete,
            onboarding_status=onboarding_status,
            offboarding_complete=offboarding_complete,
            offboarding_status=offboarding_status,
            created_after=created_after,
            updated_after=updated_after,
            offboarding_requested_after=offboarding_requested_after,
            offboarding_updated_after=offboarding_updated_after,
            scheduled_from=scheduled_from,
            scheduled_to=scheduled_to,
            due_only=due_only,
            cursor=cursor,
            page_size=safe_page_size + 1,
        )
        has_more = len(records) > safe_page_size
        page_records = records[:safe_page_size]
        next_cursor = None
        if has_more and page_records:
            last_record = page_records[-1]
            next_cursor = _encode_staff_cursor(last_record.get("updated_at"), last_record.get("id"))
        response.headers["X-Has-More"] = "true" if has_more else "false"
        if next_cursor:
            response.headers["X-Next-Cursor"] = next_cursor
        records = page_records
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
            scheduled_from=scheduled_from,
            scheduled_to=scheduled_to,
            due_only=due_only,
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
    payload_data.setdefault("onboarding_status", "approved")
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
    if custom_fields and not await _has_company_admin_access(current_user, company_id):
        policy = await staff_workflow_repo.get_company_workflow_policy(company_id)
        policy_config = policy.get("config") if isinstance(policy.get("config"), dict) else {}
        mapped_custom_fields = set(staff_onboarding_workflow_service._normalise_custom_field_group_mappings(policy_config).keys())
        custom_fields = {
            field_name: value
            for field_name, value in custom_fields.items()
            if field_name not in mapped_custom_fields
        }
    payload_data["company_id"] = company_id
    payload_data["onboarding_status"] = "awaiting_approval"
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
    approver_user_ids = await staff_onboarding_workflow_service.notify_staff_approval_requested(
        company_id=int(created["company_id"]),
        staff=created,
        requester_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
    )
    await audit_service.log_action(
        user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        action="staff.onboarding.requested",
        entity_type="staff",
        entity_id=int(created["id"]),
        metadata={
            "company_id": int(created["company_id"]),
            "onboarding_status": created.get("onboarding_status"),
            "approval_status": created.get("approval_status"),
            "approver_user_ids": approver_user_ids,
        },
    )
    created["workflow_status"] = await staff_onboarding_workflow_service.get_staff_workflow_status(int(created["id"]))
    return StaffResponse.model_validate(created)


@router.post("/{staff_id}/approve", response_model=StaffResponse, include_in_schema=False)
@router.post("/{staff_id}/onboarding/approve", response_model=StaffResponse)
async def approve_staff_request(
    staff_id: int,
    payload: StaffApprovalDecision,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    await _require_staff_approval_access(current_user, int(staff["company_id"]))
    comment_text = str(payload.comment or payload.reason or "").strip() or None
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
        onboarding_status="approved",
        onboarding_complete=bool(staff.get("onboarding_complete", False)),
        onboarding_completed_at=staff.get("onboarding_completed_at"),
        approval_status="approved",
        approved_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        approved_at=datetime.now(tz=timezone.utc),
        approval_notes=comment_text,
    )
    await audit_service.log_action(
        user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        action="staff.onboarding.approved",
        entity_type="staff",
        entity_id=staff_id,
        metadata={
            "company_id": int(updated["company_id"]),
            "comment": comment_text,
        },
    )
    await staff_onboarding_workflow_service.enqueue_staff_onboarding_workflow(
        company_id=int(updated["company_id"]),
        staff_id=staff_id,
        initiated_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
    )
    updated["workflow_status"] = await staff_onboarding_workflow_service.get_staff_workflow_status(staff_id)
    return StaffResponse.model_validate(updated)


@router.post("/{staff_id}/deny", response_model=StaffResponse, include_in_schema=False)
@router.post("/{staff_id}/onboarding/deny", response_model=StaffResponse)
async def deny_staff_request(
    staff_id: int,
    payload: StaffApprovalDecision,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    await _require_staff_approval_access(current_user, int(staff["company_id"]))
    reason_text = str(payload.reason or payload.comment or "").strip()
    if not reason_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Deny reason is required")
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
        onboarding_status="denied",
        onboarding_complete=False,
        onboarding_completed_at=None,
        approval_status="denied",
        approved_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        approved_at=datetime.now(tz=timezone.utc),
        approval_notes=reason_text,
    )
    execution = await staff_workflow_repo.get_execution_by_staff_id(staff_id)
    if execution:
        await staff_workflow_repo.update_execution_state(
            int(execution["id"]),
            state="denied",
            current_step="denied",
            completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            last_error=reason_text,
        )
    await audit_service.log_action(
        user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        action="staff.onboarding.denied",
        entity_type="staff",
        entity_id=staff_id,
        metadata={
            "company_id": int(updated["company_id"]),
            "reason": reason_text,
        },
    )
    updated["workflow_status"] = await staff_onboarding_workflow_service.get_staff_workflow_status(staff_id)
    return StaffResponse.model_validate(updated)


@router.post("/{staff_id}/offboarding/approve", response_model=StaffResponse)
async def approve_staff_offboarding(
    staff_id: int,
    payload: StaffApprovalDecision,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")

    if str(staff.get("account_action") or "").strip().lower() != "offboard requested":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No pending offboarding request for this staff member",
        )

    decision_notes = str(payload.comment or payload.reason or "").strip() or None
    updated = await staff_repo.update_staff(
        staff_id,
        company_id=staff["company_id"],
        first_name=staff["first_name"],
        last_name=staff["last_name"],
        email=staff["email"],
        mobile_phone=staff.get("mobile_phone"),
        date_onboarded=staff.get("date_onboarded"),
        date_offboarded=None,
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
        account_action="Offboard Approved",
        syncro_contact_id=staff.get("syncro_contact_id"),
        onboarding_status=staff_onboarding_workflow_service.STATE_OFFBOARDING_APPROVED,
        onboarding_complete=bool(staff.get("onboarding_complete", False)),
        onboarding_completed_at=staff.get("onboarding_completed_at"),
        approval_status="approved",
        approved_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        approved_at=datetime.now(tz=timezone.utc),
        approval_notes=decision_notes,
    )
    await audit_service.log_action(
        user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        action="staff.offboarding.approved",
        entity_type="staff",
        entity_id=staff_id,
        metadata={
            "company_id": int(updated["company_id"]),
            "decision_notes": decision_notes,
        },
    )
    await staff_onboarding_workflow_service.enqueue_staff_onboarding_workflow(
        company_id=int(updated["company_id"]),
        staff_id=staff_id,
        initiated_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        direction=staff_onboarding_workflow_service.DIRECTION_OFFBOARDING,
    )
    updated["workflow_status"] = await staff_onboarding_workflow_service.get_staff_workflow_status(staff_id)
    return StaffResponse.model_validate(updated)


@router.post("/{staff_id}/offboarding/deny", response_model=StaffResponse)
async def deny_staff_offboarding(
    staff_id: int,
    payload: StaffApprovalDecision,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")

    if str(staff.get("account_action") or "").strip().lower() != "offboard requested":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No pending offboarding request for this staff member",
        )

    decision_notes = str(payload.reason or payload.comment or "").strip()
    if not decision_notes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Deny reason is required")
    updated = await staff_repo.update_staff(
        staff_id,
        company_id=staff["company_id"],
        first_name=staff["first_name"],
        last_name=staff["last_name"],
        email=staff["email"],
        mobile_phone=staff.get("mobile_phone"),
        date_onboarded=staff.get("date_onboarded"),
        date_offboarded=None,
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
        account_action=None,
        syncro_contact_id=staff.get("syncro_contact_id"),
        onboarding_status=staff_onboarding_workflow_service.STATE_OFFBOARDING_DENIED,
        onboarding_complete=bool(staff.get("onboarding_complete", False)),
        onboarding_completed_at=staff.get("onboarding_completed_at"),
        approval_status="denied",
        approved_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        approved_at=datetime.now(tz=timezone.utc),
        approval_notes=decision_notes,
    )
    await audit_service.log_action(
        user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        action="staff.offboarding.denied",
        entity_type="staff",
        entity_id=staff_id,
        metadata={
            "company_id": int(updated["company_id"]),
            "decision_notes": decision_notes,
        },
    )
    updated["workflow_status"] = await staff_onboarding_workflow_service.get_staff_workflow_status(staff_id)
    return StaffResponse.model_validate(updated)


async def _confirm_external_checkpoint(
    staff_id: int,
    payload: StaffExternalCheckpointCallback,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    api_key_record: dict = Depends(require_api_key),
    _: None = Depends(require_database),
):
    company_id = int(payload.company_id)
    body_staff_id = int(payload.staff_id)
    if body_staff_id != staff_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Staff ID mismatch between path and body")
    policy = await staff_workflow_repo.get_company_workflow_policy(company_id)
    policy_config = policy.get("config") if isinstance(policy.get("config"), dict) else {}
    allowed_api_key_ids_raw = policy_config.get("external_confirmation_api_key_ids")
    allowed_api_key_ids: set[int] = set()
    if isinstance(allowed_api_key_ids_raw, list):
        for raw in allowed_api_key_ids_raw:
            try:
                allowed_api_key_ids.add(int(raw))
            except (TypeError, ValueError):
                continue
    api_key_id = int(api_key_record["id"])
    if allowed_api_key_ids and api_key_id not in allowed_api_key_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key is not allowed for this company's external checkpoint scope",
        )
    request_fingerprint = _external_confirmation_fingerprint(
        path_staff_id=staff_id,
        api_key_id=api_key_id,
        payload=payload,
    )
    created = await staff_workflow_repo.try_create_external_confirmation_idempotency(
        api_key_id=api_key_id,
        idempotency_key=idempotency_key.strip(),
        request_fingerprint=request_fingerprint,
        company_id=company_id,
        staff_id=staff_id,
    )
    if not created:
        existing = await staff_workflow_repo.get_external_confirmation_idempotency(
            api_key_id=api_key_id,
            idempotency_key=idempotency_key.strip(),
        )
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Callback with this Idempotency-Key is already being processed",
            )
        if str(existing.get("request_fingerprint") or "") != request_fingerprint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key has already been used with different callback data",
            )
        response_payload = existing.get("response_payload") if isinstance(existing.get("response_payload"), dict) else {}
        if existing.get("response_status") is not None and response_payload:
            return StaffExternalCheckpointResponse.model_validate(response_payload)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Callback with this Idempotency-Key is already being processed",
        )

    try:
        result = await staff_onboarding_workflow_service.confirm_external_checkpoint_and_resume(
            company_id=company_id,
            staff_id=staff_id,
            confirmation_token=payload.confirmation_token,
            source=payload.source.strip(),
            callback_timestamp=payload.callback_timestamp or datetime.now(timezone.utc),
            proof_reference_id=payload.proof_reference_id,
            payload_hash=payload.payload_hash,
            callback_payload=payload.callback_payload,
            confirmed_by_api_key_id=api_key_id,
        )
    except ValueError as exc:
        await staff_workflow_repo.finalize_external_confirmation_idempotency(
            api_key_id=api_key_id,
            idempotency_key=idempotency_key.strip(),
            response_status=status.HTTP_400_BAD_REQUEST,
            response_payload={"detail": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    response_payload = {
        "state": result.get("state") or "unknown",
        "executionId": int(result.get("execution_id") or 0),
        "staffId": staff_id,
        "companyId": company_id,
    }
    await staff_workflow_repo.finalize_external_confirmation_idempotency(
        api_key_id=api_key_id,
        idempotency_key=idempotency_key.strip(),
        response_status=status.HTTP_202_ACCEPTED,
        response_payload=response_payload,
    )
    return StaffExternalCheckpointResponse.model_validate(response_payload)


@router.post(
    "/external-checkpoints/confirm",
    response_model=StaffExternalCheckpointResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
async def confirm_external_checkpoint_legacy(
    payload: StaffExternalCheckpointCallback,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    api_key_record: dict = Depends(require_api_key),
    _: None = Depends(require_database),
):
    return await _confirm_external_checkpoint(
        staff_id=int(payload.staff_id),
        payload=payload,
        idempotency_key=idempotency_key,
        api_key_record=api_key_record,
        _=_,
    )


@router.post(
    "/{staff_id}/onboarding/external-confirm",
    response_model=StaffExternalCheckpointResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirm_external_checkpoint(
    staff_id: int,
    payload: StaffExternalCheckpointCallback,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    api_key_record: dict = Depends(require_api_key),
    _: None = Depends(require_database),
):
    return await _confirm_external_checkpoint(
        staff_id=staff_id,
        payload=payload,
        idempotency_key=idempotency_key,
        api_key_record=api_key_record,
        _=_,
    )


async def _get_staff_execution_or_404(staff_id: int) -> tuple[dict, dict]:
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    execution = await staff_workflow_repo.get_execution_by_staff_id(staff_id)
    if not execution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow execution not found")
    return staff, execution


@router.post(
    "/{staff_id}/workflow/rerun",
    response_model=StaffWorkflowManualActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rerun_staff_workflow_execution(
    staff_id: int,
    payload: StaffWorkflowManualActionRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=255),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    staff, execution = await _get_staff_execution_or_404(staff_id)
    await _require_staff_approval_access(current_user, int(staff["company_id"]))
    direction = str(execution.get("direction") or staff_onboarding_workflow_service.DIRECTION_ONBOARDING).strip().lower()
    result = await staff_onboarding_workflow_service.run_staff_onboarding_workflow(
        company_id=int(staff["company_id"]),
        staff_id=staff_id,
        initiated_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        direction=direction,
        scheduled_for_utc=execution.get("scheduled_for_utc"),
        requested_timezone=execution.get("requested_timezone"),
    )
    refreshed = await staff_workflow_repo.get_execution_by_staff_id(staff_id) or execution
    await audit_service.log_action(
        user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        action="staff.workflow.operator.rerun",
        entity_type="staff",
        entity_id=staff_id,
        metadata={
            "company_id": int(staff["company_id"]),
            "execution_id": int(refreshed["id"]),
            "direction": direction,
            "reason": (payload.reason if payload else None),
            "idempotency_key": (idempotency_key or "").strip() or None,
            "replay_key": _execution_action_replay_key(staff_id, int(refreshed["id"]), "rerun", idempotency_key),
            "result_state": result.get("state"),
        },
    )
    return StaffWorkflowManualActionResponse.model_validate(
        {
            "state": str(result.get("state") or refreshed.get("state") or "requested"),
            "executionId": int(refreshed["id"]),
            "staffId": staff_id,
            "companyId": int(staff["company_id"]),
            "idempotentReplay": False,
            "detail": "Workflow rerun requested",
        }
    )


@router.post(
    "/{staff_id}/workflow/retry-failed-step",
    response_model=StaffWorkflowManualActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_staff_workflow_failed_step(
    staff_id: int,
    payload: StaffWorkflowManualActionRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=255),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    staff, execution = await _get_staff_execution_or_404(staff_id)
    await _require_staff_approval_access(current_user, int(staff["company_id"]))
    execution_state = str(execution.get("state") or "").strip().lower()
    failed_states = {
        staff_onboarding_workflow_service.STATE_FAILED,
        staff_onboarding_workflow_service.STATE_OFFBOARDING_FAILED,
    }
    if execution_state not in failed_states:
        return StaffWorkflowManualActionResponse.model_validate(
            {
                "state": execution_state or "unknown",
                "executionId": int(execution["id"]),
                "staffId": staff_id,
                "companyId": int(staff["company_id"]),
                "idempotentReplay": True,
                "detail": "Execution is not in a failed state",
            }
        )
    result = await staff_onboarding_workflow_service.resume_staff_onboarding_workflow_after_external_confirmation(
        company_id=int(staff["company_id"]),
        staff_id=staff_id,
        execution_id=int(execution["id"]),
        initiated_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
    )
    await audit_service.log_action(
        user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        action="staff.workflow.operator.retry_failed_step",
        entity_type="staff",
        entity_id=staff_id,
        metadata={
            "company_id": int(staff["company_id"]),
            "execution_id": int(execution["id"]),
            "reason": (payload.reason if payload else None),
            "idempotency_key": (idempotency_key or "").strip() or None,
            "replay_key": _execution_action_replay_key(staff_id, int(execution["id"]), "retry_failed_step", idempotency_key),
            "result_state": result.get("state"),
        },
    )
    return StaffWorkflowManualActionResponse.model_validate(
        {
            "state": str(result.get("state") or "requested"),
            "executionId": int(execution["id"]),
            "staffId": staff_id,
            "companyId": int(staff["company_id"]),
            "idempotentReplay": False,
            "detail": "Failed workflow step retry requested",
        }
    )


@router.post(
    "/{staff_id}/workflow/resume",
    response_model=StaffWorkflowManualActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_staff_workflow_execution(
    staff_id: int,
    payload: StaffWorkflowManualActionRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=255),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    staff, execution = await _get_staff_execution_or_404(staff_id)
    await _require_staff_approval_access(current_user, int(staff["company_id"]))
    execution_state = str(execution.get("state") or "").strip().lower()
    pausable_states = {
        staff_onboarding_workflow_service.STATE_WAITING_EXTERNAL,
        staff_onboarding_workflow_service.STATE_OFFBOARDING_WAITING_EXTERNAL,
        staff_onboarding_workflow_service.STATE_PROVISIONING,
        staff_onboarding_workflow_service.STATE_OFFBOARDING_IN_PROGRESS,
    }
    if execution_state not in pausable_states:
        return StaffWorkflowManualActionResponse.model_validate(
            {
                "state": execution_state or "unknown",
                "executionId": int(execution["id"]),
                "staffId": staff_id,
                "companyId": int(staff["company_id"]),
                "idempotentReplay": True,
                "detail": "Execution is not resumable",
            }
        )
    result = await staff_onboarding_workflow_service.resume_staff_onboarding_workflow_after_external_confirmation(
        company_id=int(staff["company_id"]),
        staff_id=staff_id,
        execution_id=int(execution["id"]),
        initiated_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
    )
    await audit_service.log_action(
        user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        action="staff.workflow.operator.resume",
        entity_type="staff",
        entity_id=staff_id,
        metadata={
            "company_id": int(staff["company_id"]),
            "execution_id": int(execution["id"]),
            "reason": (payload.reason if payload else None),
            "idempotency_key": (idempotency_key or "").strip() or None,
            "replay_key": _execution_action_replay_key(staff_id, int(execution["id"]), "resume", idempotency_key),
            "result_state": result.get("state"),
        },
    )
    return StaffWorkflowManualActionResponse.model_validate(
        {
            "state": str(result.get("state") or "requested"),
            "executionId": int(execution["id"]),
            "staffId": staff_id,
            "companyId": int(staff["company_id"]),
            "idempotentReplay": False,
            "detail": "Workflow resume requested",
        }
    )


@router.post(
    "/{staff_id}/workflow/force-complete-step",
    response_model=StaffWorkflowManualActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def force_complete_staff_workflow_step(
    staff_id: int,
    payload: StaffWorkflowManualActionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=255),
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    staff, execution = await _get_staff_execution_or_404(staff_id)
    step_name = str(payload.step_name or execution.get("current_step") or "").strip()
    if ":" in step_name:
        step_name = step_name.split(":", 1)[1].strip()
    if not step_name or step_name in {"failed", "completed", "queued"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A valid stepName is required")
    await staff_workflow_repo.append_step_log(
        execution_id=int(execution["id"]),
        step_name=step_name,
        status="success",
        attempt=1,
        request_payload={"operator_forced": True, "reason": payload.reason},
        response_payload={"forced_complete": True},
    )
    result = await staff_onboarding_workflow_service.resume_staff_onboarding_workflow_after_external_confirmation(
        company_id=int(staff["company_id"]),
        staff_id=staff_id,
        execution_id=int(execution["id"]),
        initiated_by_user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
    )
    await audit_service.log_action(
        user_id=int(current_user.get("id")) if current_user.get("id") is not None else None,
        action="staff.workflow.operator.force_complete_step",
        entity_type="staff",
        entity_id=staff_id,
        metadata={
            "company_id": int(staff["company_id"]),
            "execution_id": int(execution["id"]),
            "step_name": step_name,
            "reason": payload.reason,
            "idempotency_key": (idempotency_key or "").strip() or None,
            "replay_key": _execution_action_replay_key(staff_id, int(execution["id"]), f"force_complete_step:{step_name}", idempotency_key),
            "result_state": result.get("state"),
        },
    )
    return StaffWorkflowManualActionResponse.model_validate(
        {
            "state": str(result.get("state") or "requested"),
            "executionId": int(execution["id"]),
            "staffId": staff_id,
            "companyId": int(staff["company_id"]),
            "idempotentReplay": False,
            "detail": f"Step '{step_name}' force-completed",
        }
    )


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
    if status_value in {
        staff_onboarding_workflow_service.STATE_APPROVED,
        staff_onboarding_workflow_service.STATE_OFFBOARDING_APPROVED,
    }:
        await staff_onboarding_workflow_service.enqueue_staff_onboarding_workflow(
            company_id=int(updated["company_id"]),
            staff_id=staff_id,
            initiated_by_user_id=int(__.get("id")) if __.get("id") is not None else None,
            direction=(
                staff_onboarding_workflow_service.DIRECTION_OFFBOARDING
                if status_value == staff_onboarding_workflow_service.STATE_OFFBOARDING_APPROVED
                else staff_onboarding_workflow_service.DIRECTION_ONBOARDING
            ),
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
