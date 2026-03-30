from __future__ import annotations

import asyncio
import json
import re
import secrets
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

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
STATE_OFFBOARDING_AWAITING_APPROVAL = "offboarding_awaiting_approval"
STATE_OFFBOARDING_APPROVED = "offboarding_approved"
STATE_OFFBOARDING_DENIED = "offboarding_denied"
STATE_OFFBOARDING_WAITING_EXTERNAL = "offboarding_waiting_external"
STATE_OFFBOARDING_IN_PROGRESS = "offboarding_in_progress"
STATE_OFFBOARDING_COMPLETED = "offboarding_completed"
STATE_OFFBOARDING_FAILED = "offboarding_failed"

DIRECTION_ONBOARDING = "onboarding"
DIRECTION_OFFBOARDING = "offboarding"
_VAR_PATTERN = re.compile(r"\$\{vars\.([a-zA-Z0-9_.-]+)\}")
_SECRET_KEY_TOKENS = ("password", "secret", "token", "key")


class WorkflowStepError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        step_name: str | None = None,
        request_payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.step_name = step_name
        self.request_payload = request_payload or {}


class LicenseExhaustionError(WorkflowStepError):
    pass


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _resolve_timezone(value: str | None) -> tuple[ZoneInfo | None, str | None]:
    zone_name = str(value or "").strip()
    if not zone_name:
        return None, None
    try:
        return ZoneInfo(zone_name), zone_name
    except ZoneInfoNotFoundError:
        return None, None


def _to_utc_naive(value: datetime, *, requested_zone: ZoneInfo | None) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    if requested_zone is not None:
        return value.replace(tzinfo=requested_zone).astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _compute_scheduled_execution(
    *,
    staff: dict[str, Any],
    direction: str,
    requested_timezone: str | None,
) -> tuple[datetime | None, str | None]:
    requested_zone, requested_zone_name = _resolve_timezone(requested_timezone)
    if direction == DIRECTION_OFFBOARDING:
        offboard_local = _parse_datetime(staff.get("date_offboarded"))
        if offboard_local is None:
            return None, requested_zone_name
        scheduled_for_utc = _to_utc_naive(offboard_local, requested_zone=requested_zone)
        return scheduled_for_utc, requested_zone_name

    onboard_local = _parse_datetime(staff.get("date_onboarded"))
    if onboard_local is None:
        return None, requested_zone_name

    if onboard_local.tzinfo is not None:
        local_zone = requested_zone or onboard_local.tzinfo
        onboard_local = onboard_local.astimezone(local_zone)
    else:
        local_zone = requested_zone or timezone.utc
        onboard_local = onboard_local.replace(tzinfo=local_zone)

    run_date_local = (onboard_local - timedelta(days=3)).date()
    scheduled_local = datetime.combine(run_date_local, time.min, tzinfo=local_zone)
    return scheduled_local.astimezone(timezone.utc).replace(tzinfo=None), requested_zone_name


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
    error_context: dict[str, Any] | None = None,
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
    if error_context:
        context_lines = [
            f"- {key}: {value}"
            for key, value in sorted(error_context.items())
            if value not in (None, "")
        ]
        if context_lines:
            description += "\nContext:\n" + "\n".join(context_lines) + "\n"
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


def _parse_timeout_hours(value: Any, *, default_hours: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default_hours
    return max(1, parsed)


async def _escalate_stale_non_actionable_state(
    *,
    company_id: int,
    staff: dict[str, Any],
    onboarding_status: str,
    initiated_by_user_id: int | None,
) -> dict[str, Any]:
    policy = await workflow_repo.get_company_workflow_policy(company_id)
    policy_config = policy.get("config") if isinstance(policy.get("config"), dict) else {}
    timeout_by_state = {
        STATE_AWAITING_APPROVAL: _parse_timeout_hours(
            policy_config.get("awaiting_approval_timeout_hours"),
            default_hours=48,
        ),
        STATE_WAITING_EXTERNAL: _parse_timeout_hours(
            policy_config.get("waiting_external_timeout_hours"),
            default_hours=24,
        ),
        STATE_OFFBOARDING_AWAITING_APPROVAL: _parse_timeout_hours(
            policy_config.get("offboarding_awaiting_approval_timeout_hours"),
            default_hours=48,
        ),
        STATE_OFFBOARDING_WAITING_EXTERNAL: _parse_timeout_hours(
            policy_config.get("offboarding_waiting_external_timeout_hours"),
            default_hours=24,
        ),
    }
    timeout_hours = timeout_by_state.get(onboarding_status)
    if timeout_hours is None:
        return {
            "state": "ignored",
            "reason": "not_actionable",
            "required_state": f"{STATE_APPROVED}|{STATE_PROVISIONING}",
            "current_state": onboarding_status or None,
        }

    now = _utc_now_naive()
    candidate_timestamps = [
        staff.get("updated_at"),
        staff.get("requested_at"),
        staff.get("created_at"),
    ]
    stale_anchor: datetime | None = None
    for timestamp in candidate_timestamps:
        if isinstance(timestamp, datetime):
            stale_anchor = timestamp.replace(tzinfo=None)
            break
        if isinstance(timestamp, str) and timestamp.strip():
            try:
                stale_anchor = datetime.fromisoformat(timestamp.strip().replace("Z", "+00:00")).replace(tzinfo=None)
                break
            except ValueError:
                continue
    if stale_anchor is None:
        return {
            "state": "ignored",
            "reason": "missing_stale_anchor",
            "required_state": f"{STATE_APPROVED}|{STATE_PROVISIONING}",
            "current_state": onboarding_status or None,
        }

    stale_age_hours = max(0.0, (now - stale_anchor).total_seconds() / 3600)
    if stale_age_hours < timeout_hours:
        return {
            "state": "ignored",
            "reason": "within_timeout_window",
            "required_state": f"{STATE_APPROVED}|{STATE_PROVISIONING}",
            "current_state": onboarding_status or None,
            "stale_age_hours": round(stale_age_hours, 2),
            "timeout_hours": timeout_hours,
        }

    execution = await workflow_repo.get_execution_by_staff_id(int(staff["id"]))
    if execution and execution.get("helpdesk_ticket_id"):
        return {
            "state": "ignored",
            "reason": "already_escalated",
            "required_state": f"{STATE_APPROVED}|{STATE_PROVISIONING}",
            "current_state": onboarding_status or None,
            "helpdesk_ticket_id": execution.get("helpdesk_ticket_id"),
        }

    error_text = (
        f"Onboarding request is stale in state '{onboarding_status}' "
        f"for {round(stale_age_hours, 2)} hours (timeout: {timeout_hours} hours)."
    )
    ticket_id = await _create_failure_ticket(
        company_id=company_id,
        staff=staff,
        error_text=error_text,
        error_context={
            "escalation_reason": "stale_non_actionable_state",
            "current_state": onboarding_status,
            "stale_age_hours": round(stale_age_hours, 2),
            "timeout_hours": timeout_hours,
        },
    )
    if execution:
        await workflow_repo.update_execution_state(
            int(execution["id"]),
            state=onboarding_status,
            current_step=f"escalated_{onboarding_status}",
            last_error=error_text,
            helpdesk_ticket_id=ticket_id,
        )
    await audit_service.log_action(
        user_id=initiated_by_user_id,
        action="staff.onboarding.workflow.escalated",
        entity_type="staff",
        entity_id=int(staff["id"]),
        metadata={
            "company_id": company_id,
            "current_state": onboarding_status,
            "stale_age_hours": round(stale_age_hours, 2),
            "timeout_hours": timeout_hours,
            "helpdesk_ticket_id": ticket_id,
        },
    )
    log_warning(
        "Escalated stale staff onboarding workflow state",
        company_id=company_id,
        staff_id=staff.get("id"),
        current_state=onboarding_status,
        stale_age_hours=round(stale_age_hours, 2),
        timeout_hours=timeout_hours,
        helpdesk_ticket_id=ticket_id,
    )
    return {
        "state": "escalated",
        "reason": "stale_non_actionable_state",
        "current_state": onboarding_status,
        "helpdesk_ticket_id": ticket_id,
        "stale_age_hours": round(stale_age_hours, 2),
        "timeout_hours": timeout_hours,
    }


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
    raise WorkflowStepError(
        str(last_error) if last_error else f"{step_name} failed",
        step_name=step_name,
        request_payload=request_payload,
    )


def _is_secret_var(name: str) -> bool:
    lowered = name.strip().lower()
    return any(token in lowered for token in _SECRET_KEY_TOKENS)


def _get_nested_value(payload: Any, path: str) -> Any:
    current = payload
    for part in [piece for piece in str(path or "").split(".") if piece]:
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current.get(part)
            continue
        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
            continue
        return None
    return current


def _resolve_template_value(raw: Any, *, vars_map: dict[str, Any]) -> Any:
    if isinstance(raw, str):
        exact_match = _VAR_PATTERN.fullmatch(raw.strip())
        if exact_match:
            return vars_map.get(exact_match.group(1))

        def _replace(match: re.Match[str]) -> str:
            value = vars_map.get(match.group(1))
            if value is None:
                return ""
            return str(value)

        return _VAR_PATTERN.sub(_replace, raw)
    if isinstance(raw, dict):
        return {key: _resolve_template_value(value, vars_map=vars_map) for key, value in raw.items()}
    if isinstance(raw, list):
        return [_resolve_template_value(item, vars_map=vars_map) for item in raw]
    return raw


def _redact_payload(payload: Any, *, secret_vars: set[str]) -> Any:
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if _is_secret_var(str(key)) or str(key) in secret_vars:
                redacted[key] = "***redacted***"
            else:
                redacted[key] = _redact_payload(value, secret_vars=secret_vars)
        return redacted
    if isinstance(payload, list):
        return [_redact_payload(item, secret_vars=secret_vars) for item in payload]
    return payload


def _default_workflow_steps(direction: str) -> list[dict[str, Any]]:
    if direction == DIRECTION_OFFBOARDING:
        return [{"name": "offboard_account", "type": "offboard_account"}]
    return [
        {"name": "provision_account", "type": "provision_account"},
        {"name": "assign_license", "type": "m365_assign_license"},
    ]


def _normalise_workflow_steps(policy_config: dict[str, Any], *, direction: str) -> list[dict[str, Any]]:
    configured = policy_config.get("steps")
    if not isinstance(configured, list) or not configured:
        configured = _default_workflow_steps(direction)
    steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(configured):
        if not isinstance(raw_step, dict):
            continue
        config = raw_step.get("config") if isinstance(raw_step.get("config"), dict) else raw_step
        enabled = bool(raw_step.get("enabled", config.get("enabled", True)))
        if not enabled:
            continue
        step_type = str(config.get("type") or raw_step.get("type") or raw_step.get("key") or "").strip().lower()
        if not step_type:
            continue
        step_name = str(raw_step.get("name") or config.get("name") or f"step_{index + 1}_{step_type}").strip()
        step_record = dict(config)
        step_record["name"] = step_name
        step_record["type"] = step_type
        steps.append(step_record)
    return steps


async def _execute_policy_step(
    *,
    step: dict[str, Any],
    company_id: int,
    staff: dict[str, Any],
    policy_config: dict[str, Any],
    vars_map: dict[str, Any],
) -> dict[str, Any]:
    step_type = str(step.get("type") or "").strip().lower()
    if step_type == "provision_account":
        return await _run_provisioning_step(company_id=company_id, staff=staff)
    if step_type == "offboard_account":
        return await _run_offboarding_step(company_id=company_id, staff=staff, policy_config=policy_config)
    if step_type == "m365_assign_license":
        return await _run_licensing_step(company_id=company_id, staff=staff, policy_config=policy_config)

    if step_type in {"http_get", "http_post"}:
        method = "GET" if step_type == "http_get" else "POST"
        url = str(_resolve_template_value(step.get("url"), vars_map=vars_map) or "").strip()
        if not url:
            raise WorkflowStepError("HTTP step requires url")
        headers = _resolve_template_value(step.get("headers") or {}, vars_map=vars_map)
        body = _resolve_template_value(step.get("body") or step.get("json") or {}, vars_map=vars_map)
        timeout_seconds = max(1, int(step.get("timeout_seconds") or 30))
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(method, url, headers=headers, json=body if method == "POST" else None)
        payload: dict[str, Any] = {"status_code": response.status_code}
        content_type = str(response.headers.get("content-type") or "").lower()
        if "json" in content_type:
            payload["body"] = response.json()
        else:
            payload["body"] = response.text[:4000]
        if response.status_code >= 400:
            raise WorkflowStepError(f"HTTP {method} failed ({response.status_code})", request_payload={"url": url})
        return payload

    if step_type == "m365_create_user":
        email = str(staff.get("email") or "").strip().lower()
        if not email:
            raise WorkflowStepError("Staff email is required for m365_create_user")
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        nickname = email.split("@", 1)[0]
        password = secrets.token_urlsafe(18)
        payload = {
            "accountEnabled": True,
            "displayName": " ".join(part for part in [staff.get("first_name"), staff.get("last_name")] if part).strip() or email,
            "mailNickname": str(step.get("mail_nickname") or nickname).strip(),
            "userPrincipalName": str(step.get("user_principal_name") or email).strip(),
            "passwordProfile": {
                "forceChangePasswordNextSignIn": bool(step.get("force_password_change", True)),
                "password": password,
            },
        }
        result = await m365_service._graph_post(access_token, "https://graph.microsoft.com/v1.0/users", payload)  # pyright: ignore[reportPrivateUsage]
        return {"m365_user_id": result.get("id"), "user_principal_name": payload["userPrincipalName"], "generated_password": password}

    if step_type == "m365_add_group":
        group_id = str(_resolve_template_value(step.get("group_id"), vars_map=vars_map) or "").strip()
        m365_user_id = str(_resolve_template_value(step.get("user_id"), vars_map=vars_map) or vars_map.get("m365_user_id") or "").strip()
        if not group_id or not m365_user_id:
            raise WorkflowStepError("m365_add_group requires group_id and user_id")
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        await m365_service._graph_post(  # pyright: ignore[reportPrivateUsage]
            access_token,
            f"https://graph.microsoft.com/v1.0/groups/{group_id}/members/$ref",
            {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{m365_user_id}"},
        )
        return {"group_id": group_id, "m365_user_id": m365_user_id, "added": True}

    if step_type == "create_ticket":
        subject = str(_resolve_template_value(step.get("subject"), vars_map=vars_map) or "Staff onboarding workflow checkpoint").strip()
        description = str(_resolve_template_value(step.get("description"), vars_map=vars_map) or "").strip()
        status_value = await tickets_service.resolve_status_or_default(None)
        ticket = await tickets_service.create_ticket(
            subject=subject,
            description=description or subject,
            requester_id=None,
            company_id=company_id,
            assigned_user_id=None,
            priority=str(step.get("priority") or "normal"),
            status=status_value,
            category=str(step.get("category") or "staff-onboarding"),
            module_slug=str(step.get("module_slug") or "m365"),
            external_reference=f"staff-workflow:{staff.get('id')}:{step.get('name')}",
        )
        return {"ticket_id": ticket.get("id")}

    if step_type == "conditional_pause":
        left = _resolve_template_value(step.get("if"), vars_map=vars_map)
        equals = _resolve_template_value(step.get("equals"), vars_map=vars_map)
        if left == equals:
            return {"pause": True, "reason": str(step.get("reason") or "condition matched")}
        return {"pause": False}

    raise WorkflowStepError(f"Unsupported workflow step type: {step_type}")


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


async def _graph_patch(access_token: str, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.patch(url, headers=headers, json=payload)
    if response.status_code not in (200, 204):
        raise WorkflowStepError(
            f"Microsoft Graph PATCH failed ({response.status_code})",
            request_payload={"url": url, "payload": payload},
        )
    if response.status_code == 204:
        return {}
    return response.json()


async def _resolve_staff_m365_user(company_id: int, staff: dict[str, Any]) -> dict[str, Any]:
    email = str(staff.get("email") or "").strip().lower()
    if not email:
        raise WorkflowStepError("Staff email is required for offboarding")
    users = await m365_service.get_all_users(company_id)
    matched = next(
        (
            user
            for user in users
            if str(user.get("mail") or user.get("userPrincipalName") or "").strip().lower() == email
        ),
        None,
    )
    if not matched or not matched.get("id"):
        raise WorkflowStepError(f"Unable to locate M365 user for {email}")
    return matched


async def _run_offboarding_step(
    *,
    company_id: int,
    staff: dict[str, Any],
    policy_config: dict[str, Any],
) -> dict[str, Any]:
    disable_sign_in = bool(policy_config.get("offboarding_disable_sign_in", True))
    remove_licenses = bool(policy_config.get("offboarding_remove_licenses", True))
    remove_groups = bool(policy_config.get("offboarding_remove_groups", True))
    configured_group_ids = [
        str(group_id).strip()
        for group_id in (policy_config.get("offboarding_group_ids") or [])
        if str(group_id).strip()
    ]

    user = await _resolve_staff_m365_user(company_id, staff)
    user_id = str(user["id"])
    access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
    steps_executed: list[str] = []
    removed_license_count = 0
    removed_group_count = 0

    if disable_sign_in:
        await _graph_patch(
            access_token,
            f"https://graph.microsoft.com/v1.0/users/{user_id}",
            {"accountEnabled": False},
        )
        steps_executed.append("disable_sign_in")

    if remove_licenses:
        license_payload = await m365_service._graph_get(  # pyright: ignore[reportPrivateUsage]
            access_token,
            f"https://graph.microsoft.com/v1.0/users/{user_id}/licenseDetails",
        )
        sku_ids = [entry.get("skuId") for entry in (license_payload.get("value") or []) if entry.get("skuId")]
        if sku_ids:
            await m365_service._graph_post(  # pyright: ignore[reportPrivateUsage]
                access_token,
                f"https://graph.microsoft.com/v1.0/users/{user_id}/assignLicense",
                {"addLicenses": [], "removeLicenses": sku_ids},
            )
            removed_license_count = len(sku_ids)
        steps_executed.append("remove_licenses")

    if remove_groups:
        group_ids = configured_group_ids
        if not group_ids:
            membership_payload = await m365_service._graph_get(  # pyright: ignore[reportPrivateUsage]
                access_token,
                f"https://graph.microsoft.com/v1.0/users/{user_id}/memberOf?$select=id",
            )
            group_ids = [str(item.get("id")).strip() for item in (membership_payload.get("value") or []) if item.get("id")]
        for group_id in group_ids:
            await m365_service._graph_delete(  # pyright: ignore[reportPrivateUsage]
                access_token,
                f"https://graph.microsoft.com/v1.0/groups/{group_id}/members/{user_id}/$ref",
            )
            removed_group_count += 1
        steps_executed.append("remove_groups")

    return {
        "company_id": int(company_id),
        "staff_id": int(staff["id"]),
        "offboarded": True,
        "m365_user_id": user_id,
        "steps_executed": steps_executed,
        "licenses_removed": removed_license_count,
        "groups_removed": removed_group_count,
    }


async def _execute_policy_steps(
    *,
    execution_id: int,
    company_id: int,
    staff: dict[str, Any],
    direction: str,
    policy_config: dict[str, Any],
    max_retries: int,
    waiting_external_state: str,
) -> dict[str, Any]:
    steps = _normalise_workflow_steps(policy_config, direction=direction)
    prior_logs = (await workflow_repo.list_step_logs_for_execution_ids([execution_id])).get(execution_id, [])
    succeeded_steps = {str(item.get("step_name")) for item in prior_logs if str(item.get("status")) == "success"}
    vars_map: dict[str, Any] = {
        "company_id": company_id,
        "staff_id": int(staff["id"]),
        "staff_email": staff.get("email"),
    }
    secret_vars: set[str] = set()

    for item in prior_logs:
        if str(item.get("status")) != "success":
            continue
        response_payload = item.get("response_payload")
        if isinstance(response_payload, str):
            try:
                response_payload = json.loads(response_payload)
            except Exception:  # noqa: BLE001
                response_payload = {}
        if isinstance(response_payload, dict) and isinstance(response_payload.get("context_patch"), dict):
            patch = response_payload["context_patch"]
            for key, value in (patch.get("vars") or {}).items():
                vars_map[str(key)] = value
            for name in (patch.get("secret_vars") or []):
                secret_vars.add(str(name))

    for index, step in enumerate(steps):
        step_name = str(step.get("name") or f"step_{index + 1}")
        if step_name in succeeded_steps:
            continue
        if str(step.get("type")).strip().lower() == "wait_external_checkpoint":
            confirmation_token = secrets.token_urlsafe(32)
            await workflow_repo.create_external_checkpoint(
                execution_id=execution_id,
                company_id=company_id,
                staff_id=int(staff["id"]),
                confirmation_token_hash=hash_api_key(confirmation_token),
            )
            await workflow_repo.update_execution_state(
                execution_id,
                state=waiting_external_state,
                current_step=f"{index}:{step_name}",
            )
            return {"paused": True, "confirmation_token": confirmation_token}

        resolved_step = _resolve_template_value(step, vars_map=vars_map)
        request_payload = _redact_payload(resolved_step, secret_vars=secret_vars)
        response_payload = await _attempt_step(
            execution_id=execution_id,
            step_name=step_name,
            max_retries=max_retries,
            request_payload=request_payload if isinstance(request_payload, dict) else {"request": request_payload},
            callback=lambda: _execute_policy_step(
                step=resolved_step if isinstance(resolved_step, dict) else step,
                company_id=company_id,
                staff=staff,
                policy_config=policy_config,
                vars_map=vars_map,
            ),
        )
        step_outputs = response_payload if isinstance(response_payload, dict) else {"result": response_payload}
        context_patch: dict[str, Any] = {"vars": {}, "secret_vars": []}
        output_var = str(step.get("output_var") or "").strip()
        if output_var:
            vars_map[output_var] = step_outputs
            context_patch["vars"][output_var] = step_outputs
            if _is_secret_var(output_var):
                secret_vars.add(output_var)
                context_patch["secret_vars"].append(output_var)
        if isinstance(step.get("store"), dict):
            for variable_name, source_path in step["store"].items():
                value = _get_nested_value(step_outputs, str(source_path))
                vars_map[str(variable_name)] = value
                if _is_secret_var(str(variable_name)):
                    secret_vars.add(str(variable_name))
                    context_patch["secret_vars"].append(str(variable_name))
                else:
                    context_patch["vars"][str(variable_name)] = value
        if isinstance(step_outputs, dict):
            for key, value in step_outputs.items():
                if key in {"generated_password", "secret", "token"}:
                    secret_vars.add(key)
                vars_map[key] = value
                if _is_secret_var(key):
                    context_patch["secret_vars"].append(key)
                else:
                    context_patch["vars"][key] = value
        await workflow_repo.append_step_log(
            execution_id=execution_id,
            step_name=f"{step_name}:context",
            status="success",
            attempt=1,
            request_payload={"source_step": step_name},
            response_payload={"context_patch": _redact_payload(context_patch, secret_vars=secret_vars)},
        )
        if bool(step_outputs.get("pause")):
            await workflow_repo.update_execution_state(
                execution_id,
                state=waiting_external_state,
                current_step=f"{index}:{step_name}",
                last_error=str(step_outputs.get("reason") or "paused"),
            )
            return {"paused": True, "confirmation_token": None}
        await workflow_repo.update_execution_state(
            execution_id,
            state=STATE_OFFBOARDING_IN_PROGRESS if direction == DIRECTION_OFFBOARDING else STATE_PROVISIONING,
            current_step=f"{index + 1}:{step_name}",
        )
    return {"paused": False}


async def run_staff_onboarding_workflow(
    *,
    company_id: int,
    staff_id: int,
    initiated_by_user_id: int | None,
    direction: str = DIRECTION_ONBOARDING,
    scheduled_for_utc: datetime | None = None,
    requested_timezone: str | None = None,
) -> dict[str, Any]:
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise ValueError("Staff not found")
    onboarding_status = str(staff.get("onboarding_status") or "").strip().lower()
    if direction == DIRECTION_OFFBOARDING:
        actionable_states = {STATE_OFFBOARDING_APPROVED, STATE_OFFBOARDING_IN_PROGRESS}
        stale_states = {STATE_OFFBOARDING_AWAITING_APPROVAL, STATE_OFFBOARDING_WAITING_EXTERNAL}
        waiting_external_state = STATE_OFFBOARDING_WAITING_EXTERNAL
    else:
        actionable_states = {STATE_APPROVED, STATE_PROVISIONING}
        stale_states = {STATE_AWAITING_APPROVAL, STATE_WAITING_EXTERNAL}
        waiting_external_state = STATE_WAITING_EXTERNAL

    if onboarding_status not in actionable_states:
        if onboarding_status in stale_states:
            return await _escalate_stale_non_actionable_state(
                company_id=company_id,
                staff=staff,
                onboarding_status=onboarding_status,
                initiated_by_user_id=initiated_by_user_id,
            )
        return {
            "state": "ignored",
            "reason": "not_actionable",
            "required_state": "|".join(sorted(actionable_states)),
            "current_state": onboarding_status or None,
            "direction": direction,
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
        direction=direction,
        scheduled_for_utc=scheduled_for_utc,
        requested_timezone=requested_timezone,
    )
    execution_id = int(execution["id"])

    requires_external_confirmation = bool(
        policy_config.get("requires_external_confirmation")
        if direction == DIRECTION_ONBOARDING
        else policy_config.get("requires_onprem_confirmation")
    )
    if requires_external_confirmation and not isinstance(policy_config.get("steps"), list):
        policy_config["steps"] = [{"name": "await_external_confirmation", "type": "wait_external_checkpoint"}]

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

    direction = str((await workflow_repo.get_execution_by_staff_id(staff_id) or {}).get("direction") or DIRECTION_ONBOARDING).strip().lower()
    if direction not in {DIRECTION_ONBOARDING, DIRECTION_OFFBOARDING}:
        direction = DIRECTION_ONBOARDING
    in_progress_state = STATE_OFFBOARDING_IN_PROGRESS if direction == DIRECTION_OFFBOARDING else STATE_PROVISIONING
    completed_state = STATE_OFFBOARDING_COMPLETED if direction == DIRECTION_OFFBOARDING else STATE_COMPLETED
    failed_state = STATE_OFFBOARDING_FAILED if direction == DIRECTION_OFFBOARDING else STATE_FAILED

    await workflow_repo.update_execution_state(
        execution_id,
        state=in_progress_state,
        current_step="offboarding_pipeline" if direction == DIRECTION_OFFBOARDING else "provision_account",
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
        onboarding_status=in_progress_state,
        onboarding_complete=False,
        onboarding_completed_at=None,
    )

    try:
        execution_result = await _execute_policy_steps(
            execution_id=execution_id,
            company_id=company_id,
            staff=staff,
            direction=direction,
            policy_config=policy_config,
            max_retries=max_retries,
            waiting_external_state=STATE_OFFBOARDING_WAITING_EXTERNAL if direction == DIRECTION_OFFBOARDING else STATE_WAITING_EXTERNAL,
        )
        if execution_result.get("paused"):
            paused_state = STATE_OFFBOARDING_WAITING_EXTERNAL if direction == DIRECTION_OFFBOARDING else STATE_WAITING_EXTERNAL
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
                onboarding_status=paused_state,
                onboarding_complete=False,
                onboarding_completed_at=None,
            )
            return {
                "state": paused_state,
                "execution_id": execution_id,
                "confirmation_token": execution_result.get("confirmation_token"),
            }

        completed_at = _utc_now_naive()
        await workflow_repo.update_execution_state(
            execution_id,
            state=completed_state,
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
            date_offboarded=completed_at if direction == DIRECTION_OFFBOARDING else staff.get("date_offboarded"),
            enabled=False if direction == DIRECTION_OFFBOARDING else bool(staff.get("enabled", True)),
            is_ex_staff=True if direction == DIRECTION_OFFBOARDING else bool(staff.get("is_ex_staff", False)),
            street=staff.get("street"),
            city=staff.get("city"),
            state=staff.get("state"),
            postcode=staff.get("postcode"),
            country=staff.get("country"),
            department=staff.get("department"),
            job_title=staff.get("job_title"),
            org_company=staff.get("org_company"),
            manager_name=staff.get("manager_name"),
            syncro_contact_id=staff.get("syncro_contact_id"),
            onboarding_status=completed_state,
            onboarding_complete=direction == DIRECTION_ONBOARDING,
            onboarding_completed_at=completed_at if direction == DIRECTION_ONBOARDING else None,
            account_action="Offboard Completed" if direction == DIRECTION_OFFBOARDING else staff.get("account_action"),
        )
        await audit_service.log_action(
            user_id=initiated_by_user_id,
            action=f"staff.{direction}.workflow.completed",
            entity_type="staff",
            entity_id=staff_id,
            metadata={
                "company_id": company_id,
                "execution_id": execution_id,
                "workflow_key": workflow_key,
            },
        )
        return {"state": completed_state, "execution_id": execution_id}
    except Exception as exc:  # noqa: BLE001
        error_text = str(exc)
        failed_step = exc.step_name if isinstance(exc, WorkflowStepError) else None
        failed_payload = exc.request_payload if isinstance(exc, WorkflowStepError) else None

        ticket_id: int | None = None
        try:
            ticket_id = await _create_failure_ticket(
                company_id=company_id,
                staff=staff,
                error_text=error_text,
                error_context={
                    "execution_id": execution_id,
                    "workflow_key": workflow_key,
                    "current_state": in_progress_state,
                    "step": failed_step or "provisioning_pipeline",
                    "payload": failed_payload,
                },
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
            state=failed_state,
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
            onboarding_status=failed_state,
            onboarding_complete=False,
            onboarding_completed_at=None,
        )
        await audit_service.log_action(
            user_id=initiated_by_user_id,
            action=f"staff.{direction}.workflow.failed",
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
        return {"state": failed_state, "execution_id": execution_id, "error": error_text}


async def enqueue_staff_onboarding_workflow(
    *,
    company_id: int,
    staff_id: int,
    initiated_by_user_id: int | None,
    direction: str = DIRECTION_ONBOARDING,
    requested_timezone: str | None = None,
) -> None:
    staff = await staff_repo.get_staff_by_id(staff_id)
    scheduled_for_utc, normalized_timezone = _compute_scheduled_execution(
        staff=staff or {},
        direction=direction,
        requested_timezone=requested_timezone,
    )
    policy = await workflow_repo.get_company_workflow_policy(company_id)
    execution = await workflow_repo.create_or_reset_execution(
        company_id=company_id,
        staff_id=staff_id,
        workflow_key=str(policy.get("workflow_key") or workflow_repo.DEFAULT_WORKFLOW_KEY),
        direction=direction,
        scheduled_for_utc=scheduled_for_utc,
        requested_timezone=normalized_timezone,
    )
    queued_state = STATE_OFFBOARDING_APPROVED if direction == DIRECTION_OFFBOARDING else STATE_APPROVED
    await workflow_repo.update_execution_state(
        int(execution["id"]),
        state=queued_state,
        current_step="queued",
        retries_used=0,
        last_error=None,
        completed_at=None,
    )
    log_info(
        "Queued staff onboarding workflow execution",
        company_id=company_id,
        staff_id=staff_id,
        initiated_by_user_id=initiated_by_user_id,
        direction=direction,
        execution_id=int(execution["id"]),
        state=queued_state,
        scheduled_for_utc=scheduled_for_utc.isoformat() if isinstance(scheduled_for_utc, datetime) else None,
        requested_timezone=normalized_timezone,
    )


async def process_due_approved_executions(*, limit: int = 20) -> dict[str, int]:
    processed = 0
    skipped = 0
    while processed < max(1, int(limit)):
        execution = await workflow_repo.claim_next_due_approved_execution(now_utc=_utc_now_naive())
        if not execution:
            break
        try:
            await run_staff_onboarding_workflow(
                company_id=int(execution["company_id"]),
                staff_id=int(execution["staff_id"]),
                initiated_by_user_id=None,
                direction=str(execution.get("direction") or DIRECTION_ONBOARDING),
                scheduled_for_utc=execution.get("scheduled_for_utc"),
                requested_timezone=execution.get("requested_timezone"),
            )
            processed += 1
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            log_error(
                "Failed processing due approved staff workflow execution",
                execution_id=execution.get("id"),
                company_id=execution.get("company_id"),
                staff_id=execution.get("staff_id"),
                error=str(exc),
            )
    return {"processed": processed, "skipped": skipped}


async def get_staff_workflow_status(staff_id: int) -> dict[str, Any] | None:
    execution = await workflow_repo.get_execution_by_staff_id(staff_id)
    if not execution:
        return None
    return {
        "direction": execution.get("direction") or DIRECTION_ONBOARDING,
        "state": execution.get("state"),
        "current_step": execution.get("current_step"),
        "retries_used": execution.get("retries_used"),
        "last_error": execution.get("last_error"),
        "helpdesk_ticket_id": execution.get("helpdesk_ticket_id"),
        "started_at": execution.get("started_at"),
        "completed_at": execution.get("completed_at"),
        "requested_at": execution.get("requested_at"),
        "scheduled_for_utc": execution.get("scheduled_for_utc"),
        "requested_timezone": execution.get("requested_timezone"),
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
    execution_state = str(execution.get("state") or "").strip().lower()
    if execution_state not in {STATE_WAITING_EXTERNAL, STATE_OFFBOARDING_WAITING_EXTERNAL}:
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
