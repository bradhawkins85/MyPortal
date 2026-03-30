from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone
from typing import Any

from app.core.logging import log_error, log_info, log_warning
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import licenses as license_repo
from app.repositories import staff as staff_repo
from app.repositories import staff_onboarding_workflows as workflow_repo
from app.services import audit as audit_service
from app.services import m365 as m365_service
from app.services import notifications as notifications_service
from app.services import tickets as tickets_service
from app.security.api_keys import hash_api_key


STATE_REQUESTED = "requested"
STATE_AWAITING_APPROVAL = "awaiting_approval"
STATE_APPROVED = "approved"
STATE_DENIED = "denied"
STATE_WAITING_EXTERNAL = "waiting_external"
STATE_PROVISIONING = "provisioning"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"


class WorkflowStepError(RuntimeError):
    pass


class LicenseExhaustionError(WorkflowStepError):
    pass


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def resolve_approver_user_ids(
    *,
    company_id: int,
    policy: dict[str, Any] | None = None,
) -> list[int]:
    policy_config = (policy or {}).get("config") if isinstance((policy or {}).get("config"), dict) else {}
    approver_user_ids_raw = policy_config.get("approver_user_ids")
    designated_ids: set[int] = set()
    if isinstance(approver_user_ids_raw, list):
        for raw_id in approver_user_ids_raw:
            try:
                designated_ids.add(int(raw_id))
            except (TypeError, ValueError):
                continue

    memberships = await membership_repo.list_company_memberships(company_id)
    company_admin_ids: set[int] = set()
    permission_based_ids: set[int] = set()
    approver_permission = str(policy_config.get("approver_permission") or "staff.approve").strip()
    for membership in memberships:
        if str(membership.get("status") or "").lower() != "active":
            continue
        try:
            member_user_id = int(membership.get("user_id"))
        except (TypeError, ValueError):
            continue
        permissions = set(membership.get("combined_permissions") or membership.get("permissions") or [])
        if bool(membership.get("is_admin")) or "company.admin" in permissions:
            company_admin_ids.add(member_user_id)
        if approver_permission and approver_permission in permissions:
            permission_based_ids.add(member_user_id)

    return sorted(designated_ids | permission_based_ids | company_admin_ids)


async def notify_staff_approval_requested(
    *,
    company_id: int,
    staff: dict[str, Any],
    requester_user_id: int | None,
) -> list[int]:
    policy = await workflow_repo.get_company_workflow_policy(company_id)
    approver_ids = await resolve_approver_user_ids(company_id=company_id, policy=policy)
    if not approver_ids:
        return []
    staff_name = " ".join(
        part for part in [staff.get("first_name"), staff.get("last_name")] if part
    ).strip() or (staff.get("email") or f"staff #{staff.get('id')}")
    message = f"Approval requested for staff onboarding: {staff_name}."
    metadata = {
        "company_id": company_id,
        "staff_id": staff.get("id"),
        "staff_email": staff.get("email"),
        "requested_by_user_id": requester_user_id,
    }
    for approver_id in approver_ids:
        try:
            await notifications_service.emit_notification(
                event_type="staff.onboarding.approval_requested",
                message=message,
                user_id=approver_id,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001
            log_warning(
                "Failed to send staff approval request notification",
                company_id=company_id,
                staff_id=staff.get("id"),
                approver_id=approver_id,
                error=str(exc),
            )
    return approver_ids


async def _create_failure_ticket(
    *,
    company_id: int,
    staff: dict[str, Any],
    error_text: str,
) -> int | None:
    company = await company_repo.get_company_by_id(company_id)
    company_name = (company or {}).get("name") or f"Company #{company_id}"
    staff_name = " ".join(
        part for part in [staff.get("first_name"), staff.get("last_name")] if part
    ).strip() or (staff.get("email") or f"staff #{staff.get('id')}")
    description = (
        "Automated onboarding workflow failed.\n\n"
        f"Company: {company_name} (ID: {company_id})\n"
        f"Staff: {staff_name} (ID: {staff.get('id')})\n"
        f"Email: {staff.get('email') or 'n/a'}\n"
        f"Error: {error_text}\n"
    )
    status_value = await tickets_service.resolve_status_or_default(None)
    ticket = await tickets_service.create_ticket(
        subject=f"Staff onboarding workflow failed for {staff_name}",
        description=description,
        requester_id=None,
        company_id=company_id,
        assigned_user_id=None,
        priority="high",
        status=status_value,
        category="staff-onboarding",
        module_slug="m365",
        external_reference=f"staff-onboarding:{staff.get('id')}",
    )
    ticket_id = ticket.get("id")
    try:
        return int(ticket_id) if ticket_id is not None else None
    except (TypeError, ValueError):
        return None


async def _attempt_step(
    *,
    execution_id: int,
    step_name: str,
    max_retries: int,
    request_payload: dict[str, Any],
    callback,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 2):
        try:
            response_payload = await callback()
            await workflow_repo.append_step_log(
                execution_id=execution_id,
                step_name=step_name,
                status="success",
                attempt=attempt,
                request_payload=request_payload,
                response_payload=response_payload if isinstance(response_payload, dict) else {"result": str(response_payload)},
            )
            return response_payload if isinstance(response_payload, dict) else {"result": response_payload}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await workflow_repo.append_step_log(
                execution_id=execution_id,
                step_name=step_name,
                status="failed",
                attempt=attempt,
                request_payload=request_payload,
                error_message=str(exc),
            )
            if attempt > max_retries:
                break
            await asyncio.sleep(min(2 ** (attempt - 1), 5))
    raise WorkflowStepError(str(last_error) if last_error else f"{step_name} failed")


async def _run_provisioning_step(*, company_id: int, staff: dict[str, Any]) -> dict[str, Any]:
    email = (staff.get("email") or "").strip().lower()
    if not email:
        raise WorkflowStepError("Staff email is required for M365 provisioning")

    # M365 operation: verify Graph connectivity and user visibility.
    users = await m365_service.get_all_users(company_id)
    matched = next(
        (
            user
            for user in users
            if str(user.get("mail") or user.get("userPrincipalName") or "").strip().lower() == email
        ),
        None,
    )
    return {
        "matched_user": bool(matched),
        "matched_user_id": matched.get("id") if matched else None,
        "users_scanned": len(users),
    }


async def _run_licensing_step(
    *,
    company_id: int,
    staff: dict[str, Any],
    policy_config: dict[str, Any],
) -> dict[str, Any]:
    # M365 operation: refresh current license allocation snapshot.
    await m365_service.sync_company_licenses(company_id)

    required_sku = str(policy_config.get("required_license_sku") or "").strip()
    target_license_id = policy_config.get("assign_license_id")

    if required_sku:
        license_record = await license_repo.get_license_by_company_and_sku(company_id, required_sku)
        if not license_record:
            raise WorkflowStepError(f"Required license SKU not found: {required_sku}")
        target_license_id = license_record.get("id")
        allocated = int(license_record.get("allocated") or 0)
        capacity = int(license_record.get("count") or 0)
        if allocated >= capacity:
            raise LicenseExhaustionError(
                f"License exhaustion for SKU {required_sku}: allocated={allocated}, capacity={capacity}"
            )

    if target_license_id is None:
        return {"assigned": False, "reason": "No license assignment policy configured"}

    await license_repo.link_staff_to_license(int(staff["id"]), int(target_license_id))
    return {"assigned": True, "license_id": int(target_license_id)}


async def run_staff_onboarding_workflow(
    *,
    company_id: int,
    staff_id: int,
    initiated_by_user_id: int | None,
) -> dict[str, Any]:
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise ValueError("Staff not found")
    onboarding_status = str(staff.get("onboarding_status") or "").strip().lower()
    if onboarding_status not in {STATE_APPROVED, STATE_WAITING_EXTERNAL}:
        return {
            "state": "ignored",
            "reason": "not_approved",
            "required_state": f"{STATE_APPROVED}|{STATE_WAITING_EXTERNAL}",
            "current_state": onboarding_status or None,
        }

    policy = await workflow_repo.get_company_workflow_policy(company_id)
    if not policy.get("is_enabled", True):
        return {"state": "skipped", "reason": "workflow_disabled"}

    workflow_key = str(policy.get("workflow_key") or workflow_repo.DEFAULT_WORKFLOW_KEY)
    max_retries = max(0, int(policy.get("max_retries") or 0))
    policy_config = policy.get("config") if isinstance(policy.get("config"), dict) else {}

    execution = await workflow_repo.create_or_reset_execution(
        company_id=company_id,
        staff_id=staff_id,
        workflow_key=workflow_key,
    )
    execution_id = int(execution["id"])

    requires_external_confirmation = bool(policy_config.get("requires_external_confirmation"))
    if requires_external_confirmation:
        confirmation_token = secrets.token_urlsafe(32)
        await workflow_repo.create_external_checkpoint(
            execution_id=execution_id,
            company_id=company_id,
            staff_id=staff_id,
            confirmation_token_hash=hash_api_key(confirmation_token),
        )
        await workflow_repo.update_execution_state(
            execution_id,
            state=STATE_WAITING_EXTERNAL,
            current_step="await_external_confirmation",
        )
        await staff_repo.update_staff(
            staff_id,
            company_id=company_id,
            first_name=staff.get("first_name") or "",
            last_name=staff.get("last_name") or "",
            email=staff.get("email") or "",
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
            onboarding_status=STATE_WAITING_EXTERNAL,
            onboarding_complete=False,
            onboarding_completed_at=None,
        )
        await audit_service.log_action(
            user_id=initiated_by_user_id,
            action="staff.onboarding.workflow.waiting_external",
            entity_type="staff",
            entity_id=staff_id,
            metadata={
                "company_id": company_id,
                "execution_id": execution_id,
                "workflow_key": workflow_key,
            },
        )
        return {
            "state": STATE_WAITING_EXTERNAL,
            "execution_id": execution_id,
            "confirmation_token": confirmation_token,
        }

    return await resume_staff_onboarding_workflow_after_external_confirmation(
        company_id=company_id,
        staff_id=staff_id,
        execution_id=execution_id,
        initiated_by_user_id=initiated_by_user_id,
    )


async def resume_staff_onboarding_workflow_after_external_confirmation(
    *,
    company_id: int,
    staff_id: int,
    execution_id: int,
    initiated_by_user_id: int | None,
) -> dict[str, Any]:
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise ValueError("Staff not found")
    policy = await workflow_repo.get_company_workflow_policy(company_id)
    workflow_key = str(policy.get("workflow_key") or workflow_repo.DEFAULT_WORKFLOW_KEY)
    max_retries = max(0, int(policy.get("max_retries") or 0))
    policy_config = policy.get("config") if isinstance(policy.get("config"), dict) else {}

    await workflow_repo.update_execution_state(
        execution_id,
        state=STATE_PROVISIONING,
        current_step="provision_account",
        started_at=_utc_now_naive(),
    )
    await staff_repo.update_staff(
        staff_id,
        company_id=company_id,
        first_name=staff.get("first_name") or "",
        last_name=staff.get("last_name") or "",
        email=staff.get("email") or "",
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
        onboarding_status=STATE_PROVISIONING,
        onboarding_complete=False,
        onboarding_completed_at=None,
    )

    linked_license_id: int | None = None
    try:
        await _attempt_step(
            execution_id=execution_id,
            step_name="provision_account",
            max_retries=max_retries,
            request_payload={"company_id": company_id, "staff_id": staff_id},
            callback=lambda: _run_provisioning_step(company_id=company_id, staff=staff),
        )

        licensing_result = await _attempt_step(
            execution_id=execution_id,
            step_name="assign_license",
            max_retries=max_retries,
            request_payload={
                "company_id": company_id,
                "staff_id": staff_id,
                "policy": policy_config,
            },
            callback=lambda: _run_licensing_step(
                company_id=company_id,
                staff=staff,
                policy_config=policy_config,
            ),
        )
        if licensing_result.get("license_id") is not None:
            linked_license_id = int(licensing_result["license_id"])

        completed_at = _utc_now_naive()
        await workflow_repo.update_execution_state(
            execution_id,
            state=STATE_COMPLETED,
            current_step="completed",
            retries_used=0,
            completed_at=completed_at,
            last_error=None,
        )
        await staff_repo.update_staff(
            staff_id,
            company_id=company_id,
            first_name=staff.get("first_name") or "",
            last_name=staff.get("last_name") or "",
            email=staff.get("email") or "",
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
            onboarding_status=STATE_COMPLETED,
            onboarding_complete=True,
            onboarding_completed_at=completed_at,
        )
        await audit_service.log_action(
            user_id=initiated_by_user_id,
            action="staff.onboarding.workflow.completed",
            entity_type="staff",
            entity_id=staff_id,
            metadata={
                "company_id": company_id,
                "execution_id": execution_id,
                "workflow_key": workflow_key,
                "linked_license_id": linked_license_id,
            },
        )
        return {"state": STATE_COMPLETED, "execution_id": execution_id}
    except Exception as exc:  # noqa: BLE001
        error_text = str(exc)
        if linked_license_id is not None:
            try:
                await license_repo.unlink_staff_from_license(staff_id, linked_license_id)
            except Exception as compensation_exc:  # noqa: BLE001
                log_warning(
                    "Compensation failed while unlinking staff license",
                    staff_id=staff_id,
                    license_id=linked_license_id,
                    error=str(compensation_exc),
                )

        ticket_id: int | None = None
        try:
            ticket_id = await _create_failure_ticket(
                company_id=company_id,
                staff=staff,
                error_text=error_text,
            )
        except Exception as ticket_exc:  # noqa: BLE001
            log_error(
                "Failed to create helpdesk ticket for onboarding workflow failure",
                company_id=company_id,
                staff_id=staff_id,
                error=str(ticket_exc),
            )

        await workflow_repo.update_execution_state(
            execution_id,
            state=STATE_FAILED,
            current_step="failed",
            retries_used=max_retries,
            last_error=error_text,
            helpdesk_ticket_id=ticket_id,
            completed_at=_utc_now_naive(),
        )
        await staff_repo.update_staff(
            staff_id,
            company_id=company_id,
            first_name=staff.get("first_name") or "",
            last_name=staff.get("last_name") or "",
            email=staff.get("email") or "",
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
            onboarding_status=STATE_FAILED,
            onboarding_complete=False,
            onboarding_completed_at=None,
        )
        await audit_service.log_action(
            user_id=initiated_by_user_id,
            action="staff.onboarding.workflow.failed",
            entity_type="staff",
            entity_id=staff_id,
            metadata={
                "company_id": company_id,
                "execution_id": execution_id,
                "workflow_key": workflow_key,
                "error": error_text,
                "helpdesk_ticket_id": ticket_id,
            },
        )
        log_error(
            "Staff onboarding workflow failed",
            company_id=company_id,
            staff_id=staff_id,
            execution_id=execution_id,
            error=error_text,
        )
        return {"state": STATE_FAILED, "execution_id": execution_id, "error": error_text}


async def enqueue_staff_onboarding_workflow(
    *,
    company_id: int,
    staff_id: int,
    initiated_by_user_id: int | None,
) -> None:
    log_info(
        "Queueing staff onboarding workflow",
        company_id=company_id,
        staff_id=staff_id,
        initiated_by_user_id=initiated_by_user_id,
    )
    asyncio.create_task(
        run_staff_onboarding_workflow(
            company_id=company_id,
            staff_id=staff_id,
            initiated_by_user_id=initiated_by_user_id,
        )
    )


async def get_staff_workflow_status(staff_id: int) -> dict[str, Any] | None:
    execution = await workflow_repo.get_execution_by_staff_id(staff_id)
    if not execution:
        return None
    return {
        "state": execution.get("state"),
        "current_step": execution.get("current_step"),
        "retries_used": execution.get("retries_used"),
        "last_error": execution.get("last_error"),
        "helpdesk_ticket_id": execution.get("helpdesk_ticket_id"),
        "started_at": execution.get("started_at"),
        "completed_at": execution.get("completed_at"),
        "requested_at": execution.get("requested_at"),
    }


async def confirm_external_checkpoint_and_resume(
    *,
    company_id: int,
    staff_id: int,
    confirmation_token: str,
    source: str,
    callback_timestamp: datetime,
    proof_reference_id: str | None,
    payload_hash: str | None,
    callback_payload: dict[str, Any] | None,
    confirmed_by_api_key_id: int,
) -> dict[str, Any]:
    if callback_timestamp.tzinfo is not None:
        callback_timestamp = callback_timestamp.astimezone(timezone.utc).replace(tzinfo=None)
    execution = await workflow_repo.get_execution_by_staff_id(staff_id)
    if not execution:
        raise ValueError("Workflow execution not found")
    if int(execution.get("company_id") or 0) != company_id:
        raise ValueError("Company scope mismatch")
    if str(execution.get("state") or "").strip().lower() != STATE_WAITING_EXTERNAL:
        raise ValueError("Workflow execution is not waiting for external confirmation")

    checkpoint = await workflow_repo.get_pending_external_checkpoint(
        execution_id=int(execution["id"]),
        company_id=company_id,
        staff_id=staff_id,
        confirmation_token_hash=hash_api_key(confirmation_token),
    )
    if not checkpoint:
        raise ValueError("Invalid confirmation token")

    await workflow_repo.confirm_external_checkpoint(
        int(checkpoint["id"]),
        source=source,
        callback_timestamp=callback_timestamp,
        proof_reference_id=proof_reference_id,
        payload_hash=payload_hash,
        callback_payload=callback_payload,
        confirmed_by_api_key_id=confirmed_by_api_key_id,
    )
    await audit_service.log_action(
        user_id=None,
        action="staff.onboarding.workflow.external_confirmed",
        entity_type="staff",
        entity_id=staff_id,
        metadata={
            "company_id": company_id,
            "execution_id": int(execution["id"]),
            "source": source,
            "payload_hash": payload_hash,
            "proof_reference_id": proof_reference_id,
            "confirmed_by_api_key_id": confirmed_by_api_key_id,
        },
    )
    return await resume_staff_onboarding_workflow_after_external_confirmation(
        company_id=company_id,
        staff_id=staff_id,
        execution_id=int(execution["id"]),
        initiated_by_user_id=None,
    )
