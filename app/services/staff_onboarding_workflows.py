from __future__ import annotations

import asyncio
import json
import re
import secrets
from urllib.parse import urlparse
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core.logging import log_error, log_info, log_warning
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import licenses as license_repo
from app.repositories import staff as staff_repo
from app.repositories import staff_custom_fields as staff_custom_fields_repo
from app.repositories import staff_onboarding_workflows as workflow_repo
from app.repositories import users as user_repo
from app.services import audit as audit_service
from app.services import email as email_service
from app.services import m365 as m365_service
from app.services import notifications as notifications_service
from app.services import system_variables
from app.services import tickets as tickets_service
from app.security.api_keys import hash_api_key


STATE_REQUESTED = "requested"
STATE_AWAITING_APPROVAL = "awaiting_approval"
STATE_APPROVED = "approved"
STATE_DENIED = "denied"
STATE_WAITING_EXTERNAL = "waiting_external"
STATE_PROVISIONING = "provisioning"
STATE_PAUSED_LICENSE_UNAVAILABLE = "paused_license_unavailable"
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


def _default_workflow_key(direction: str) -> str:
    return (
        workflow_repo.DEFAULT_OFFBOARDING_WORKFLOW_KEY
        if direction == DIRECTION_OFFBOARDING
        else workflow_repo.DEFAULT_WORKFLOW_KEY
    )

# Kid-friendly word list: words are stored exclusively in the database table
# workflow_kid_friendly_words (migration 199).  They are never served by any
# API or UI route; the only access path is workflow_repo.get_kid_friendly_words().
# Letter substitutions used for kid-friendly password "symbol" replacements.
_KID_SUBSTITUTIONS: dict[str, str] = {
    "a": "@",
    "i": "!",
    "s": "$",
    "e": "3",
    "o": "0",
}

# In-process cache populated on first use; avoids repeated DB round-trips while
# ensuring the word list remains inaccessible via any network interface.
_kid_words_cache: list[str] = []
_kid_words_lock = asyncio.Lock()


def _generate_strong_password(
    *,
    length: int = 16,
    use_upper: bool = True,
    use_digits: bool = True,
    use_symbols: bool = True,
) -> str:
    """Generate a cryptographically random strong password."""
    import string

    lower = string.ascii_lowercase
    upper = string.ascii_uppercase if use_upper else ""
    digits = string.digits if use_digits else ""
    symbols = "!@#$%^&*-_=+?" if use_symbols else ""
    alphabet = lower + upper + digits + symbols
    if not alphabet:
        alphabet = lower

    length = max(8, min(length, 128))

    # Guarantee at least one character from each requested category.
    required: list[str] = [secrets.choice(lower)]
    if use_upper:
        required.append(secrets.choice(upper))
    if use_digits:
        required.append(secrets.choice(digits))
    if use_symbols:
        required.append(secrets.choice(symbols))

    remaining = [secrets.choice(alphabet) for _ in range(length - len(required))]
    password_chars = required + remaining
    # Shuffle using secrets module for unpredictable ordering.
    for i in range(len(password_chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        password_chars[i], password_chars[j] = password_chars[j], password_chars[i]
    return "".join(password_chars)


async def _generate_kid_friendly_password() -> str:
    """Generate a kid-friendly word-based password using words from the database.

    Words are loaded from workflow_kid_friendly_words on first call and cached
    in-process; the cache is never serialised or transmitted.

    Format: two capitalised common words (first letter upper-case only) followed
    by 2-4 random digits, with 1-2 letter-to-symbol substitutions applied to
    non-leading characters across both words.  The large DB word pool (~4 000+
    entries) combined with random digit count and substitution positions provides
    many billions of possible combinations.
    """
    global _kid_words_cache

    async with _kid_words_lock:
        if not _kid_words_cache:
            _kid_words_cache = await workflow_repo.get_kid_friendly_words()

    word_pool = _kid_words_cache
    if not word_pool:
        raise WorkflowStepError("Kid-friendly word list is empty — ensure migration 199 has run")

    word1 = secrets.choice(word_pool)
    word2 = secrets.choice(word_pool)

    # Capitalise first letter of each word only (as per requirements).
    word1 = word1[0].upper() + word1[1:]
    word2 = word2[0].upper() + word2[1:]

    # Collect candidate positions for symbol substitutions across both words,
    # skipping the capitalised first letter of each word to preserve readability.
    candidates: list[tuple[int, int, str]] = []  # (word_index, char_index, replacement)
    for word_idx, word in enumerate([word1, word2]):
        for char_idx, ch in enumerate(word):
            if char_idx == 0:
                continue
            lower_ch = ch.lower()
            if lower_ch in _KID_SUBSTITUTIONS:
                candidates.append((word_idx, char_idx, _KID_SUBSTITUTIONS[lower_ch]))

    # Apply 1-2 substitutions chosen at random from available positions.
    num_substitutions = min(secrets.choice([1, 2]), len(candidates))
    available = list(candidates)
    chosen: list[tuple[int, int, str]] = []
    for _ in range(num_substitutions):
        if not available:
            break
        pick_idx = secrets.randbelow(len(available))
        chosen.append(available.pop(pick_idx))

    words = [list(word1), list(word2)]
    for word_idx, char_idx, replacement in chosen:
        words[word_idx][char_idx] = replacement

    # Guarantee at least one symbol is present even when no substitution
    # candidates were found (e.g. both words lack substitutable letters).
    if not chosen:
        sym = secrets.choice(list(_KID_SUBSTITUTIONS.values()))
        # Insert after the first character of word2 to preserve capitalization.
        if len(words[1]) > 1:
            pos = secrets.randbelow(len(words[1]) - 1) + 1
            words[1].insert(pos, sym)
        else:
            words[1].append(sym)

    final_word1 = "".join(words[0])
    final_word2 = "".join(words[1])

    # Append 2-4 random digits for extra entropy.
    num_digits = secrets.choice([2, 3, 4])
    digit_suffix = "".join(str(secrets.randbelow(10)) for _ in range(num_digits))

    return f"{final_word1}{final_word2}{digit_suffix}"


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


def _build_license_retry_metadata(
    *,
    workflow_key: str,
    execution_id: int,
    step_name: str | None,
    error_text: str,
) -> dict[str, Any]:
    return {
        "reason": "license_unavailable",
        "workflow_key": workflow_key,
        "execution_id": execution_id,
        "step": step_name or "assign_license",
        "paused_at": _utc_now_naive().isoformat(),
        "retry_trigger": "license_capacity_change",
        "error": error_text,
    }


def _should_create_license_exhaustion_ticket(policy_config: dict[str, Any]) -> bool:
    return bool(policy_config.get("create_ticket_on_license_unavailable"))


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _serialise_dt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        return value.isoformat()
    if isinstance(value, str):
        return value or None
    return str(value)


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


def _extract_timezone_name(source: dict[str, Any] | None) -> str | None:
    if not isinstance(source, dict):
        return None
    candidate_keys = ("timezone", "time_zone", "tz", "iana_timezone")
    for key in candidate_keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for container_key in ("settings", "preferences", "profile"):
        container = source.get(container_key)
        if isinstance(container, dict):
            for key in candidate_keys:
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _format_datetime_tokens(dt: datetime, *, prefix: str) -> dict[str, str]:
    return {
        f"{prefix}.iso": dt.isoformat(),
        f"{prefix}.date": dt.strftime("%Y-%m-%d"),
        f"{prefix}.display": dt.strftime("%b %d, %Y"),
    }


def _build_now_tokens(*, timezone_name: str | None) -> tuple[dict[str, str], str | None]:
    utc_now = datetime.now(timezone.utc)
    tokens = {
        "now.iso": utc_now.isoformat(),
        "now.date": utc_now.strftime("%Y-%m-%d"),
        "now.datetime_utc": utc_now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    local_zone, resolved_zone_name = _resolve_timezone(timezone_name)
    if local_zone is None:
        return tokens, None
    local_now = utc_now.astimezone(local_zone)
    tokens.update(_format_datetime_tokens(local_now, prefix="now.local"))
    tokens["now.local.datetime"] = local_now.strftime("%Y-%m-%d %H:%M:%S %Z")
    return tokens, resolved_zone_name


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
    direction: str = DIRECTION_ONBOARDING,
) -> list[int]:
    policy = await workflow_repo.get_company_workflow_policy(
        company_id, default_workflow_key=_default_workflow_key(direction)
    )
    approver_ids = await resolve_approver_user_ids(company_id=company_id, policy=policy)
    if not approver_ids:
        return []
    staff_name = " ".join(
        part for part in [staff.get("first_name"), staff.get("last_name")] if part
    ).strip() or (staff.get("email") or f"staff #{staff.get('id')}")
    direction_label = "offboarding" if direction == DIRECTION_OFFBOARDING else "onboarding"
    message = f"Approval requested for staff {direction_label}: {staff_name}."

    company = await company_repo.get_company_by_id(company_id)
    company_name = (company or {}).get("name") or f"Company #{company_id}"

    requested_by: str | None = None
    if requester_user_id is not None:
        requester = await user_repo.get_user_by_id(requester_user_id)
        if requester:
            requested_by = " ".join(
                part for part in [requester.get("first_name"), requester.get("last_name")] if part
            ).strip() or (requester.get("email") or f"User #{requester_user_id}")
        else:
            requested_by = f"User #{requester_user_id}"

    metadata = {
        "company": company_name,
        "staff": staff_name,
        "requested_by": requested_by,
        "staff_id": staff.get("id"),
    }
    event_type = (
        "staff.offboarding.approval_requested"
        if direction == DIRECTION_OFFBOARDING
        else "staff.onboarding.approval_requested"
    )
    for approver_id in approver_ids:
        try:
            await notifications_service.emit_notification(
                event_type=event_type,
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
    secret_vars: set[str] | None = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 2):
        try:
            response_payload = await callback()
            raw_response = response_payload if isinstance(response_payload, dict) else {"result": str(response_payload)}
            # Redact any keys that look like secrets (passwords, tokens, keys) before
            # persisting to the database so that generated passwords are never stored.
            effective_secret_vars: set[str] = set(secret_vars or ())
            if isinstance(raw_response, dict):
                for key in raw_response:
                    if _is_secret_var(str(key)):
                        effective_secret_vars.add(str(key))
            log_response = _redact_payload(raw_response, secret_vars=effective_secret_vars)
            await workflow_repo.append_step_log(
                execution_id=execution_id,
                step_name=step_name,
                status="success",
                attempt=attempt,
                request_payload=request_payload,
                response_payload=log_response,
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


def _parse_json_text(value: str) -> Any:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _coerce_step_json_fields(step: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(step)
    json_field_targets = {
        "headers_json": "headers",
        "query_params_json": "query_params",
        "json_body": "json",
        "store_json": "store",
    }
    for source_field, target_field in json_field_targets.items():
        raw_value = normalized.get(source_field)
        if not isinstance(raw_value, str):
            continue
        parsed = _parse_json_text(raw_value)
        if parsed is not None:
            normalized[target_field] = parsed
    return normalized


def _resolve_json_object_candidate(raw: Any, *, field_name: str) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        parsed = _parse_json_text(raw)
        if isinstance(parsed, dict):
            return parsed
    raise WorkflowStepError(f"{field_name} must be a JSON object")


def _validate_web_url(value: str, *, field_name: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WorkflowStepError(f"{field_name} must be a valid http/https URL")
    return url


def _normalize_plain_text_payload(raw_text: str) -> str:
    # Keep external data as plain text only, avoid HTML/script interpretation in downstream consumers.
    normalized = str(raw_text or "")
    normalized = normalized.replace("\x00", "")
    normalized = normalized.replace("<", "&lt;").replace(">", "&gt;")
    return normalized[:4000]


def _default_workflow_steps(direction: str) -> list[dict[str, Any]]:
    if direction == DIRECTION_OFFBOARDING:
        return [
            {"name": "disable_and_cleanup_account", "type": "offboard_account"},
            {"name": "remove_from_teams_groups", "type": "m365_remove_teams_group_member"},
            {"name": "remove_from_sharepoint_sites", "type": "m365_remove_sharepoint_site_member"},
            {"name": "rename_identity", "type": "m365_rename_upn_display_name"},
            {"name": "update_org_fields", "type": "m365_update_org_fields"},
            {"name": "hide_from_gal", "type": "m365_hide_from_gal"},
            {"name": "identity_hygiene", "type": "m365_identity_hygiene"},
        ]
    return [
        {"name": "provision_account", "type": "provision_account"},
        {"name": "assign_license", "type": "m365_assign_license"},
        {"name": "add_to_teams_groups", "type": "m365_add_teams_group_member"},
        {"name": "add_to_sharepoint_sites", "type": "m365_add_sharepoint_site_member"},
    ]


def _normalise_workflow_steps(policy_config: dict[str, Any], *, direction: str) -> list[dict[str, Any]]:
    configured_key = "offboarding_steps" if direction == DIRECTION_OFFBOARDING else "steps"
    configured = policy_config.get(configured_key)
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


def _resolve_step_max_retries(step: dict[str, Any], *, default_max_retries: int) -> int:
    retry_policy = step.get("retry_policy") if isinstance(step.get("retry_policy"), dict) else {}
    value = retry_policy.get("max_retries", step.get("max_retries", default_max_retries))
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default_max_retries))


def _resolve_step_failure_mode(step: dict[str, Any]) -> str:
    failure_policy = step.get("failure_policy") if isinstance(step.get("failure_policy"), dict) else {}
    mode = str(failure_policy.get("mode") or "fail_fast").strip().lower()
    if mode not in {"fail_fast", "continue"}:
        return "fail_fast"
    return mode


def _normalize_condition_values(raw: Any) -> list[str]:
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list):
        values = [str(item or "").strip() for item in raw]
    else:
        return []
    return [value for value in values if value]


def _evaluate_step_conditions(
    *,
    step: dict[str, Any],
    staff: dict[str, Any],
    staff_custom_fields: dict[str, Any],
) -> tuple[bool, str | None]:
    conditions = step.get("conditions") if isinstance(step.get("conditions"), dict) else {}
    if not conditions:
        return True, None

    allowed_departments = {
        value.lower() for value in _normalize_condition_values(conditions.get("department_in"))
    }
    if allowed_departments:
        staff_department = str(staff.get("department") or "").strip().lower()
        if staff_department not in allowed_departments:
            return False, "department_not_in_scope"

    required_custom_fields = _normalize_condition_values(conditions.get("custom_fields_truthy"))
    for field_name in required_custom_fields:
        if not _is_truthy_custom_field(staff_custom_fields.get(field_name)):
            return False, f"custom_field_not_truthy:{field_name}"
    return True, None


def _normalize_group_ids(raw_group_ids: Any) -> list[str]:
    if isinstance(raw_group_ids, str):
        raw_group_ids = [item.strip() for item in raw_group_ids.split(",") if item.strip()]
    if not isinstance(raw_group_ids, list):
        return []
    normalized: list[str] = []
    for raw_group_id in raw_group_ids:
        group_id = str(raw_group_id or "").strip()
        if group_id:
            normalized.append(group_id)
    return normalized


def _normalise_custom_field_group_mappings(policy_config: dict[str, Any]) -> dict[str, list[str]]:
    raw_mappings = (
        policy_config.get("custom_field_group_mappings")
        or policy_config.get("customFieldGroupMappings")
        or {}
    )
    mappings: dict[str, list[str]] = {}
    if isinstance(raw_mappings, dict):
        iterable = raw_mappings.items()
    elif isinstance(raw_mappings, list):
        iterable = []
        for item in raw_mappings:
            if not isinstance(item, dict):
                continue
            field_name = (
                item.get("field_name")
                or item.get("field")
                or item.get("custom_field_name")
                or item.get("customFieldName")
            )
            iterable.append(
                (
                    field_name,
                    item.get("group_ids") or item.get("groupIds") or item.get("groups") or item.get("group_id"),
                )
            )
    else:
        return mappings

    for raw_field_name, raw_group_ids in iterable:
        field_name = str(raw_field_name or "").strip()
        if not field_name:
            continue
        group_ids = _normalize_group_ids(raw_group_ids)
        if not group_ids:
            continue
        mappings[field_name] = group_ids
    return mappings


def _is_truthy_custom_field(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "checked"}


async def _execute_custom_field_group_memberships(
    *,
    execution_id: int,
    company_id: int,
    staff: dict[str, Any],
    custom_fields: dict[str, Any],
    policy_config: dict[str, Any],
    vars_map: dict[str, Any],
    max_retries: int,
) -> dict[str, Any]:
    mappings = _normalise_custom_field_group_mappings(policy_config)
    if not mappings:
        return {"executed": False, "groups_added": []}

    selected_groups: list[tuple[str, str]] = []
    for field_name, group_ids in mappings.items():
        if not _is_truthy_custom_field(custom_fields.get(field_name)):
            continue
        for group_id in group_ids:
            selected_groups.append((field_name, group_id))
    if not selected_groups:
        return {"executed": False, "groups_added": []}

    m365_user_id = str(vars_map.get("m365_user_id") or "").strip()
    if not m365_user_id:
        resolved_user = await _resolve_staff_m365_user(company_id, staff)
        m365_user_id = str(resolved_user.get("id") or "").strip()
    if not m365_user_id:
        raise WorkflowStepError("Unable to resolve M365 user for custom-field group assignments")

    added_groups: list[str] = []
    for field_name, group_id in selected_groups:
        step_name = f"custom_field_group:{field_name}:{group_id}"
        await _attempt_step(
            execution_id=execution_id,
            step_name=step_name,
            max_retries=max_retries,
            request_payload={"field_name": field_name, "group_id": group_id, "m365_user_id": m365_user_id},
            callback=lambda group_id=group_id: _execute_policy_step(
                step={"type": "m365_add_group", "group_id": group_id, "user_id": m365_user_id},
                company_id=company_id,
                staff=staff,
                policy_config=policy_config,
                vars_map=vars_map,
            ),
        )
        added_groups.append(group_id)
    vars_map["m365_groups_added_from_custom_fields"] = added_groups
    return {"executed": True, "groups_added": added_groups}


def _set_nested_payload_value(payload: dict[str, Any], *, path: str, value: Any) -> None:
    parts = [part.strip() for part in path.split(".") if part.strip()]
    if not parts:
        return
    cursor = payload
    for part in parts[:-1]:
        if not isinstance(cursor.get(part), dict):
            cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = value


async def _execute_policy_step(
    *,
    step: dict[str, Any],
    company_id: int,
    staff: dict[str, Any],
    policy_config: dict[str, Any],
    vars_map: dict[str, Any],
) -> dict[str, Any]:
    async def _resolve_step_user_id() -> str:
        configured_user_id = str(
            _resolve_template_value(step.get("user_id"), vars_map=vars_map) or vars_map.get("m365_user_id") or ""
        ).strip()
        if configured_user_id:
            return configured_user_id
        user = await _resolve_staff_m365_user(company_id, staff)
        return str(user["id"])

    step_type = str(step.get("type") or "").strip().lower()
    if step_type == "provision_account":
        return await _run_provisioning_step(company_id=company_id, staff=staff)
    if step_type == "offboard_account":
        return await _run_offboarding_step(company_id=company_id, staff=staff, policy_config=policy_config)
    if step_type == "m365_assign_license":
        return await _run_licensing_step(company_id=company_id, staff=staff, policy_config=policy_config)

    if step_type in {"http_get", "http_post"}:
        method = "GET" if step_type == "http_get" else "POST"
        url = _validate_web_url(_resolve_template_value(step.get("url"), vars_map=vars_map), field_name="url")
        headers = _resolve_template_value(step.get("headers") or {}, vars_map=vars_map)
        headers = _resolve_json_object_candidate(headers, field_name="headers")
        query_params = _resolve_template_value(
            step.get("query_params") or step.get("query") or step.get("params") or {},
            vars_map=vars_map,
        )
        query_params = _resolve_json_object_candidate(query_params, field_name="query_params")
        body = _resolve_template_value(
            step.get("json") if step.get("json") is not None else (step.get("body") or {}),
            vars_map=vars_map,
        )
        if isinstance(body, str):
            parsed_body = _parse_json_text(body)
            if parsed_body is not None:
                body = parsed_body
        timeout_seconds = max(1, int(step.get("timeout_seconds") or 30))
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=query_params or None,
                json=body if method == "POST" else None,
            )
        payload: dict[str, Any] = {"status_code": response.status_code}
        payload["body"] = _normalize_plain_text_payload(response.text)
        if response.status_code >= 400:
            raise WorkflowStepError(f"HTTP {method} failed ({response.status_code})", request_payload={"url": url})
        return payload

    if step_type == "curl_text":
        url = _validate_web_url(_resolve_template_value(step.get("url"), vars_map=vars_map), field_name="url")
        timeout_seconds = max(1, int(step.get("timeout_seconds") or 30))
        process = await asyncio.create_subprocess_exec(
            "curl",
            "--silent",
            "--show-error",
            "--location",
            "--max-time",
            str(timeout_seconds),
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        body_text = _normalize_plain_text_payload(stdout.decode("utf-8", errors="replace"))
        payload = {"status_code": int(process.returncode or 0), "body": body_text}
        if process.returncode != 0:
            err_text = _normalize_plain_text_payload(stderr.decode("utf-8", errors="replace"))
            raise WorkflowStepError(
                f"CURL request failed (exit {process.returncode})",
                request_payload={"url": url, "error": err_text},
            )
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
        m365_user_id = await _resolve_step_user_id()
        if not group_id or not m365_user_id:
            raise WorkflowStepError("m365_add_group requires group_id and user_id")
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        await m365_service._graph_post(  # pyright: ignore[reportPrivateUsage]
            access_token,
            f"https://graph.microsoft.com/v1.0/groups/{group_id}/members/$ref",
            {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{m365_user_id}"},
        )
        return {"group_id": group_id, "m365_user_id": m365_user_id, "added": True}

    if step_type in {"m365_add_teams_group_member", "m365_remove_teams_group_member"}:
        group_ids = _normalize_group_ids(
            _resolve_template_value(step.get("group_ids"), vars_map=vars_map)
            or _resolve_template_value(step.get("group_ids_csv"), vars_map=vars_map)
            or _resolve_template_value(step.get("group_id"), vars_map=vars_map)
        )
        if not group_ids:
            raise WorkflowStepError(f"{step_type} requires one or more group IDs")
        m365_user_id = await _resolve_step_user_id()
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        changed_group_ids: list[str] = []
        for group_id in group_ids:
            if step_type == "m365_add_teams_group_member":
                await m365_service._graph_post(  # pyright: ignore[reportPrivateUsage]
                    access_token,
                    f"https://graph.microsoft.com/v1.0/groups/{group_id}/members/$ref",
                    {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{m365_user_id}"},
                )
            else:
                await m365_service._graph_delete(  # pyright: ignore[reportPrivateUsage]
                    access_token,
                    f"https://graph.microsoft.com/v1.0/groups/{group_id}/members/{m365_user_id}/$ref",
                )
            changed_group_ids.append(group_id)
        return {
            "m365_user_id": m365_user_id,
            "group_ids": changed_group_ids,
            "operation": "add" if step_type == "m365_add_teams_group_member" else "remove",
        }

    if step_type in {"m365_add_sharepoint_site_member", "m365_remove_sharepoint_site_member"}:
        site_ids = _normalize_group_ids(
            _resolve_template_value(step.get("site_ids"), vars_map=vars_map)
            or _resolve_template_value(step.get("site_ids_csv"), vars_map=vars_map)
            or _resolve_template_value(step.get("site_id"), vars_map=vars_map)
        )
        if not site_ids:
            raise WorkflowStepError(f"{step_type} requires one or more site IDs")
        m365_user_id = await _resolve_step_user_id()
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        site_role = str(_resolve_template_value(step.get("site_role"), vars_map=vars_map) or "write").strip().lower()
        if site_role not in {"read", "write"}:
            raise WorkflowStepError("site_role must be either 'read' or 'write'")
        changed_site_ids: list[str] = []
        for site_id in site_ids:
            if step_type == "m365_add_sharepoint_site_member":
                await m365_service._graph_post(  # pyright: ignore[reportPrivateUsage]
                    access_token,
                    f"https://graph.microsoft.com/v1.0/sites/{site_id}/permissions",
                    {
                        "roles": [site_role],
                        "grantedToIdentitiesV2": [
                            {"user": {"id": m365_user_id}},
                        ],
                    },
                )
            else:
                permissions = await m365_service._graph_get(  # pyright: ignore[reportPrivateUsage]
                    access_token,
                    f"https://graph.microsoft.com/v1.0/sites/{site_id}/permissions",
                )
                for permission in permissions.get("value") or []:
                    permission_id = str(permission.get("id") or "").strip()
                    if not permission_id:
                        continue
                    granted_entries = permission.get("grantedToIdentitiesV2") or []
                    user_ids = {
                        str((entry.get("user") or {}).get("id") or "").strip()
                        for entry in granted_entries
                        if isinstance(entry, dict)
                    }
                    if m365_user_id in user_ids:
                        await m365_service._graph_delete(  # pyright: ignore[reportPrivateUsage]
                            access_token,
                            f"https://graph.microsoft.com/v1.0/sites/{site_id}/permissions/{permission_id}",
                        )
            changed_site_ids.append(site_id)
        return {
            "m365_user_id": m365_user_id,
            "site_ids": changed_site_ids,
            "operation": "add" if step_type == "m365_add_sharepoint_site_member" else "remove",
            "site_role": site_role,
        }

    if step_type == "m365_rename_upn_display_name":
        user = await _resolve_staff_m365_user(company_id, staff)
        user_id = str(user["id"])
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        current_display_name = str(user.get("displayName") or "").strip()
        current_upn = str(user.get("userPrincipalName") or user.get("mail") or "").strip()
        upn_prefix = str(_resolve_template_value(step.get("upn_prefix"), vars_map=vars_map) or "former").strip()
        upn_local, _, upn_domain = current_upn.partition("@")
        next_upn = current_upn
        if upn_domain and upn_local:
            next_upn = f"{upn_prefix}.{upn_local}@{upn_domain}" if upn_prefix else current_upn
        next_display_name = str(
            _resolve_template_value(step.get("display_name"), vars_map=vars_map)
            or f"{current_display_name} (Former Staff)"
        ).strip()
        patch_payload = {
            "displayName": next_display_name,
            "userPrincipalName": next_upn,
        }
        await _graph_patch(access_token, f"https://graph.microsoft.com/v1.0/users/{user_id}", patch_payload)
        return {
            "m365_user_id": user_id,
            "renamed": True,
            "previous": {"displayName": current_display_name, "userPrincipalName": current_upn},
            "updated": patch_payload,
        }

    if step_type == "m365_update_org_fields":
        user = await _resolve_staff_m365_user(company_id, staff)
        user_id = str(user["id"])
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        next_department = str(
            _resolve_template_value(step.get("department"), vars_map=vars_map) or step.get("department_value") or "Former Staff"
        ).strip()
        next_company = str(
            _resolve_template_value(step.get("company_name"), vars_map=vars_map) or step.get("company_value") or "Former Staff"
        ).strip()
        patch_payload = {
            "department": next_department,
            "companyName": next_company,
        }
        await _graph_patch(access_token, f"https://graph.microsoft.com/v1.0/users/{user_id}", patch_payload)
        return {
            "m365_user_id": user_id,
            "updated": patch_payload,
        }

    if step_type == "m365_hide_from_gal":
        user = await _resolve_staff_m365_user(company_id, staff)
        user_id = str(user["id"])
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        property_path = str(step.get("property_path") or "showInAddressList").strip() or "showInAddressList"
        hidden_value = bool(step.get("hidden", True))
        patch_payload: dict[str, Any] = {}
        _set_nested_payload_value(patch_payload, path=property_path, value=not hidden_value)
        await _graph_patch(access_token, f"https://graph.microsoft.com/v1.0/users/{user_id}", patch_payload)
        return {
            "m365_user_id": user_id,
            "property_path": property_path,
            "hidden": hidden_value,
            "updated": patch_payload,
        }

    if step_type == "m365_identity_hygiene":
        user = await _resolve_staff_m365_user(company_id, staff)
        user_id = str(user["id"])
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        hygiene_updates = step.get("hygiene_updates") if isinstance(step.get("hygiene_updates"), dict) else {
            "officeLocation": "Offboarded",
            "jobTitle": "Former Staff",
            "mobilePhone": None,
            "businessPhones": [],
        }
        patch_payload = _resolve_template_value(hygiene_updates, vars_map=vars_map)
        if not isinstance(patch_payload, dict):
            raise WorkflowStepError("m365_identity_hygiene requires hygiene_updates object")
        await _graph_patch(access_token, f"https://graph.microsoft.com/v1.0/users/{user_id}", patch_payload)
        revoked_sessions = False
        if bool(step.get("revoke_sign_in_sessions", True)):
            await m365_service._graph_post(  # pyright: ignore[reportPrivateUsage]
                access_token,
                f"https://graph.microsoft.com/v1.0/users/{user_id}/revokeSignInSessions",
                {},
            )
            revoked_sessions = True
        return {
            "m365_user_id": user_id,
            "updated": patch_payload,
            "revoke_sign_in_sessions": revoked_sessions,
        }

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

    if step_type in {"send_welcome_email", "send_custom_email", "email_requestor"}:
        recipients = _normalize_email_recipients(_resolve_template_value(step.get("to") or step.get("recipients"), vars_map=vars_map))
        if step_type == "send_welcome_email" and not recipients:
            recipients = _normalize_email_recipients(vars_map.get("staff_email"))
        if step_type == "email_requestor" and not recipients:
            recipients = _normalize_email_recipients(vars_map.get("requestor_email"))
        if not recipients:
            raise WorkflowStepError(f"{step_type} requires at least one recipient")

        subject = str(_resolve_template_value(step.get("subject"), vars_map=vars_map) or "").strip()
        if not subject:
            subject = "Welcome to the team" if step_type == "send_welcome_email" else "Staff onboarding update"

        html_body = str(
            _resolve_template_value(step.get("html_body") or step.get("body_html") or step.get("body"), vars_map=vars_map) or ""
        ).strip()
        text_body = str(_resolve_template_value(step.get("text_body"), vars_map=vars_map) or "").strip() or None
        if not html_body and not text_body:
            raise WorkflowStepError(f"{step_type} requires html_body/body or text_body")
        if not html_body and text_body:
            html_body = text_body.replace("\n", "<br>")

        sent, provider_metadata = await email_service.send_email(
            subject=subject,
            recipients=recipients,
            html_body=html_body,
            text_body=text_body,
            sender=str(_resolve_template_value(step.get("from"), vars_map=vars_map) or "").strip() or None,
            reply_to=str(_resolve_template_value(step.get("reply_to"), vars_map=vars_map) or "").strip() or None,
        )
        if not sent:
            raise WorkflowStepError("Email delivery was skipped or failed")
        return {
            "email_sent": True,
            "email_type": step_type,
            "recipients": recipients,
            "subject": subject,
            "provider_metadata": provider_metadata or {},
        }

    if step_type == "generate_password":
        pw_length = max(8, min(int(step.get("length") or 16), 128))
        use_upper = bool(step.get("use_upper", True))
        use_digits = bool(step.get("use_digits", True))
        use_symbols = bool(step.get("use_symbols", True))
        generated = _generate_strong_password(
            length=pw_length,
            use_upper=use_upper,
            use_digits=use_digits,
            use_symbols=use_symbols,
        )
        var_name = str(step.get("output_var") or "generated_password").strip() or "generated_password"
        return {"generated_password": generated, var_name: generated}

    if step_type == "generate_kid_friendly_password":
        generated = await _generate_kid_friendly_password()
        var_name = str(step.get("output_var") or "generated_password").strip() or "generated_password"
        return {"generated_password": generated, var_name: generated}

    if step_type == "create_user":
        email = str(staff.get("email") or "").strip().lower()
        if not email:
            raise WorkflowStepError("Staff email is required for create_user")
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        nickname = email.split("@", 1)[0]
        raw_password = str(_resolve_template_value(step.get("password"), vars_map=vars_map) or "").strip()
        if not raw_password:
            raw_password = secrets.token_urlsafe(18)
        display_name = str(
            _resolve_template_value(step.get("display_name"), vars_map=vars_map)
            or " ".join(p for p in [staff.get("first_name"), staff.get("last_name")] if p).strip()
            or email
        ).strip()
        upn = str(
            _resolve_template_value(step.get("user_principal_name"), vars_map=vars_map) or email
        ).strip()
        mail_nickname = str(
            _resolve_template_value(step.get("mail_nickname"), vars_map=vars_map) or nickname
        ).strip()
        user_payload: dict[str, Any] = {
            "accountEnabled": bool(step.get("account_enabled", True)),
            "displayName": display_name,
            "mailNickname": mail_nickname,
            "userPrincipalName": upn,
            "passwordProfile": {
                "forceChangePasswordNextSignIn": bool(step.get("force_change_password_next_signin", True)),
                "password": raw_password,
            },
        }
        given_name = str(_resolve_template_value(step.get("given_name"), vars_map=vars_map) or staff.get("first_name") or "").strip()
        if given_name:
            user_payload["givenName"] = given_name
        surname = str(_resolve_template_value(step.get("surname"), vars_map=vars_map) or staff.get("last_name") or "").strip()
        if surname:
            user_payload["surname"] = surname
        job_title = str(_resolve_template_value(step.get("job_title"), vars_map=vars_map) or staff.get("job_title") or "").strip()
        if job_title:
            user_payload["jobTitle"] = job_title
        department = str(_resolve_template_value(step.get("department"), vars_map=vars_map) or staff.get("department") or "").strip()
        if department:
            user_payload["department"] = department
        company_name_val = str(_resolve_template_value(step.get("company_name"), vars_map=vars_map) or "").strip()
        if company_name_val:
            user_payload["companyName"] = company_name_val
        office_location = str(_resolve_template_value(step.get("office_location"), vars_map=vars_map) or "").strip()
        if office_location:
            user_payload["officeLocation"] = office_location
        usage_location = str(_resolve_template_value(step.get("usage_location"), vars_map=vars_map) or "").strip()
        if usage_location:
            user_payload["usageLocation"] = usage_location
        result = await m365_service._graph_post(access_token, "https://graph.microsoft.com/v1.0/users", user_payload)  # pyright: ignore[reportPrivateUsage]
        new_user_id = result.get("id")
        if new_user_id:
            vars_map["m365_user_id"] = str(new_user_id)
        return {"m365_user_id": new_user_id, "user_principal_name": upn, "generated_password": raw_password}

    if step_type == "assign_licenses":
        m365_user_id = await _resolve_step_user_id()
        if not m365_user_id:
            raise WorkflowStepError("assign_licenses requires a resolvable M365 user ID")
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        licenses_csv = str(
            _resolve_template_value(step.get("licenses_csv") or step.get("license_skus"), vars_map=vars_map) or ""
        ).strip()
        sku_part_numbers = [s.strip() for s in licenses_csv.split(",") if s.strip()]
        if not sku_part_numbers:
            raise WorkflowStepError("assign_licenses requires licenses_csv with at least one SKU part number")
        # Resolve subscribed SKU IDs from the tenant by matching part numbers.
        skus_response = await m365_service._graph_get(  # pyright: ignore[reportPrivateUsage]
            access_token,
            "https://graph.microsoft.com/v1.0/subscribedSkus?$select=skuId,skuPartNumber",
        )
        sku_map = {
            str(entry.get("skuPartNumber") or "").upper(): str(entry.get("skuId") or "")
            for entry in (skus_response.get("value") or [])
            if entry.get("skuId")
        }
        add_licenses = []
        for part_number in sku_part_numbers:
            sku_id = sku_map.get(part_number.upper())
            if not sku_id:
                raise WorkflowStepError(f"assign_licenses: SKU part number not found in tenant: {part_number}")
            add_licenses.append({"skuId": sku_id})
        remove_first = bool(step.get("remove_existing_licenses", False))
        remove_licenses: list[str] = []
        if remove_first:
            license_details = await m365_service._graph_get(  # pyright: ignore[reportPrivateUsage]
                access_token,
                f"https://graph.microsoft.com/v1.0/users/{m365_user_id}/licenseDetails",
            )
            remove_licenses = [
                str(entry.get("skuId"))
                for entry in (license_details.get("value") or [])
                if entry.get("skuId")
            ]
        await m365_service._graph_post(  # pyright: ignore[reportPrivateUsage]
            access_token,
            f"https://graph.microsoft.com/v1.0/users/{m365_user_id}/assignLicense",
            {"addLicenses": add_licenses, "removeLicenses": remove_licenses},
        )
        return {
            "m365_user_id": m365_user_id,
            "licenses_assigned": sku_part_numbers,
            "licenses_removed": remove_licenses,
        }

    if step_type == "add_to_groups":
        m365_user_id = await _resolve_step_user_id()
        if not m365_user_id:
            raise WorkflowStepError("add_to_groups requires a resolvable M365 user ID")
        group_ids = _normalize_group_ids(
            _resolve_template_value(step.get("group_ids_csv") or step.get("group_ids") or step.get("group_id"), vars_map=vars_map)
        )
        if not group_ids:
            raise WorkflowStepError("add_to_groups requires group_ids_csv with at least one group ID")
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        added_group_ids: list[str] = []
        for group_id in group_ids:
            await m365_service._graph_post(  # pyright: ignore[reportPrivateUsage]
                access_token,
                f"https://graph.microsoft.com/v1.0/groups/{group_id}/members/$ref",
                {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{m365_user_id}"},
            )
            added_group_ids.append(group_id)
        return {"m365_user_id": m365_user_id, "groups_added": added_group_ids}

    if step_type == "set_manager":
        m365_user_id = await _resolve_step_user_id()
        if not m365_user_id:
            raise WorkflowStepError("set_manager requires a resolvable M365 user ID")
        access_token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
        manager_id = str(_resolve_template_value(step.get("manager_id"), vars_map=vars_map) or "").strip()
        if not manager_id:
            manager_email = str(_resolve_template_value(step.get("manager_email"), vars_map=vars_map) or "").strip().lower()
            if not manager_email:
                raise WorkflowStepError("set_manager requires manager_id or manager_email")
            manager_lookup = await m365_service._graph_get(  # pyright: ignore[reportPrivateUsage]
                access_token,
                f"https://graph.microsoft.com/v1.0/users/{manager_email}?$select=id",
            )
            manager_id = str(manager_lookup.get("id") or "").strip()
            if not manager_id:
                raise WorkflowStepError(f"set_manager: unable to resolve manager from email {manager_email}")
        ref_url = f"https://graph.microsoft.com/v1.0/users/{m365_user_id}/manager/$ref"
        ref_payload = {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{manager_id}"}
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            ref_response = await client.put(ref_url, headers=headers, json=ref_payload)
        if ref_response.status_code not in (200, 204):
            raise WorkflowStepError(
                f"set_manager: Graph PUT manager/$ref failed ({ref_response.status_code})",
                request_payload={"url": ref_url},
            )
        return {"m365_user_id": m365_user_id, "manager_id": manager_id, "manager_set": True}

    raise WorkflowStepError(f"Unsupported workflow step type: {step_type}")


def _normalize_email_recipients(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, list):
        normalized: list[str] = []
        for item in raw:
            value = str(item or "").strip()
            if value:
                normalized.append(value)
        return normalized
    return []


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
    custom_field_map = await staff_custom_fields_repo.get_all_staff_field_values(
        company_id,
        [int(staff["id"])],
    )
    staff_custom_fields = custom_field_map.get(int(staff["id"]), {})
    staff_first_name = str(staff.get("first_name") or "").strip()
    staff_last_name = str(staff.get("last_name") or "").strip()
    staff_full_name = " ".join(part for part in [staff_first_name, staff_last_name] if part).strip()
    staff_email = str(staff.get("email") or "").strip().lower() or None
    staff_email_local_part = staff_email.split("@", 1)[0] if staff_email else ""
    vars_map: dict[str, Any] = {
        "company_id": company_id,
        "staff_id": int(staff["id"]),
        "staff_email": staff_email,
        "staff_custom_fields": dict(staff_custom_fields),
        "staff.first_name": staff_first_name,
        "staff.last_name": staff_last_name,
        "staff.full_name": staff_full_name,
        "staff.email": staff_email,
        "staff.email_local_part": staff_email_local_part,
        "staff.job_title": str(staff.get("job_title") or "").strip() or None,
        "staff.department": str(staff.get("department") or "").strip() or None,
        "staff.office_location": str(staff.get("office_location") or "").strip() or None,
        # Backward-compatible aliases.
        "staff_first_name": staff_first_name,
        "staff_last_name": staff_last_name,
        "staff_full_name": staff_full_name,
    }
    requestor_email = await _resolve_requestor_email(staff)
    if requestor_email:
        vars_map["requestor_email"] = requestor_email
    company = await company_repo.get_company_by_id(company_id)
    if company:
        vars_map["company_name"] = company.get("name")
    requestor_timezone_name: str | None = None
    requestor_user_id = staff.get("requested_by_user_id")
    if requestor_user_id is not None:
        try:
            requestor = await user_repo.get_user_by_id(int(requestor_user_id))
        except (TypeError, ValueError):
            requestor = None
        requestor_timezone_name = _extract_timezone_name(requestor)
    company_timezone_name = _extract_timezone_name(company)
    now_tokens, resolved_timezone_name = _build_now_tokens(
        timezone_name=company_timezone_name or requestor_timezone_name
    )
    vars_map.update(now_tokens)
    if resolved_timezone_name:
        vars_map["now.local.timezone"] = resolved_timezone_name
    for name, value in system_variables.get_system_variables().items():
        vars_map[f"system.{name}"] = value
    for name, value in staff_custom_fields.items():
        vars_map[f"custom_fields.{name}"] = value
        vars_map[f"staff_custom_fields.{name}"] = value
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
        if isinstance(resolved_step, dict):
            resolved_step = _coerce_step_json_fields(resolved_step)
        should_execute, skip_reason = _evaluate_step_conditions(
            step=resolved_step if isinstance(resolved_step, dict) else step,
            staff=staff,
            staff_custom_fields=staff_custom_fields,
        )
        if not should_execute:
            await workflow_repo.append_step_log(
                execution_id=execution_id,
                step_name=step_name,
                status="skipped",
                attempt=1,
                request_payload={
                    "conditions": (resolved_step if isinstance(resolved_step, dict) else step).get("conditions") or {}
                },
                response_payload={"skipped": True, "reason": skip_reason or "conditions_not_met"},
            )
            await workflow_repo.update_execution_state(
                execution_id,
                state=STATE_OFFBOARDING_IN_PROGRESS if direction == DIRECTION_OFFBOARDING else STATE_PROVISIONING,
                current_step=f"{index + 1}:{step_name}:skipped",
            )
            continue
        request_payload = _redact_payload(resolved_step, secret_vars=secret_vars)
        step_max_retries = _resolve_step_max_retries(step, default_max_retries=max_retries)
        step_failure_mode = _resolve_step_failure_mode(step)
        try:
            response_payload = await _attempt_step(
                execution_id=execution_id,
                step_name=step_name,
                max_retries=step_max_retries,
                request_payload=request_payload if isinstance(request_payload, dict) else {"request": request_payload},
                callback=lambda: _execute_policy_step(
                    step=resolved_step if isinstance(resolved_step, dict) else step,
                    company_id=company_id,
                    staff=staff,
                    policy_config=policy_config,
                    vars_map=vars_map,
                ),
            )
        except WorkflowStepError:
            if step_failure_mode == "continue":
                await workflow_repo.update_execution_state(
                    execution_id,
                    state=STATE_OFFBOARDING_IN_PROGRESS if direction == DIRECTION_OFFBOARDING else STATE_PROVISIONING,
                    current_step=f"{index + 1}:{step_name}:failed_continue",
                )
                continue
            raise
        step_outputs = response_payload if isinstance(response_payload, dict) else {"result": response_payload}
        context_patch: dict[str, Any] = {"vars": {}, "secret_vars": []}
        effective_step = resolved_step if isinstance(resolved_step, dict) else step
        output_var = str(effective_step.get("output_var") or "").strip()
        if output_var:
            vars_map[output_var] = step_outputs
            context_patch["vars"][output_var] = step_outputs
            if _is_secret_var(output_var):
                secret_vars.add(output_var)
                context_patch["secret_vars"].append(output_var)
        if isinstance(effective_step.get("store"), dict):
            for variable_name, source_path in effective_step["store"].items():
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
    if direction == DIRECTION_ONBOARDING:
        await _execute_custom_field_group_memberships(
            execution_id=execution_id,
            company_id=company_id,
            staff=staff,
            custom_fields=staff_custom_fields,
            policy_config=policy_config,
            vars_map=vars_map,
            max_retries=max_retries,
        )
    return {"paused": False}


async def _resolve_requestor_email(staff: dict[str, Any]) -> str | None:
    requestor_user_id = staff.get("requested_by_user_id")
    if requestor_user_id is None:
        return None
    try:
        user_id = int(requestor_user_id)
    except (TypeError, ValueError):
        return None
    requestor = await user_repo.get_user_by_id(user_id)
    if not requestor:
        return None
    email = str(requestor.get("email") or "").strip().lower()
    return email or None


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

    policy = await workflow_repo.get_company_workflow_policy(
        company_id,
        default_workflow_key=_default_workflow_key(direction),
    )
    if not policy.get("is_enabled", True):
        return {"state": "skipped", "reason": "workflow_disabled"}

    workflow_key = str(policy.get("workflow_key") or _default_workflow_key(direction))
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
    direction = str((await workflow_repo.get_execution_by_id(execution_id) or {}).get("direction") or DIRECTION_ONBOARDING).strip().lower()
    if direction not in {DIRECTION_ONBOARDING, DIRECTION_OFFBOARDING}:
        direction = DIRECTION_ONBOARDING
    policy = await workflow_repo.get_company_workflow_policy(
        company_id, default_workflow_key=_default_workflow_key(direction)
    )
    workflow_key = str(policy.get("workflow_key") or _default_workflow_key(direction))
    max_retries = max(0, int(policy.get("max_retries") or 0))
    policy_config = policy.get("config") if isinstance(policy.get("config"), dict) else {}

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
    except LicenseExhaustionError as exc:
        error_text = str(exc)
        failed_step = exc.step_name if isinstance(exc, WorkflowStepError) else None
        retry_metadata = _build_license_retry_metadata(
            workflow_key=workflow_key,
            execution_id=execution_id,
            step_name=failed_step,
            error_text=error_text,
        )

        ticket_id: int | None = None
        if _should_create_license_exhaustion_ticket(policy_config):
            try:
                ticket_id = await _create_failure_ticket(
                    company_id=company_id,
                    staff=staff,
                    error_text=error_text,
                    error_context={
                        "execution_id": execution_id,
                        "workflow_key": workflow_key,
                        "current_state": in_progress_state,
                        "step": failed_step or "assign_license",
                        "pause_reason": "license_unavailable",
                    },
                )
            except Exception as ticket_exc:  # noqa: BLE001
                log_error(
                    "Failed to create ticket for paused workflow due to license exhaustion",
                    company_id=company_id,
                    staff_id=staff_id,
                    execution_id=execution_id,
                    error=str(ticket_exc),
                )

        await workflow_repo.append_step_log(
            execution_id=execution_id,
            step_name=failed_step or "assign_license",
            status="paused",
            attempt=1,
            request_payload={"pause_reason": "license_unavailable"},
            response_payload={"retry_metadata": retry_metadata},
            error_message=error_text,
        )
        await workflow_repo.update_execution_state(
            execution_id,
            state=STATE_PAUSED_LICENSE_UNAVAILABLE,
            current_step=f"paused_{failed_step or 'assign_license'}",
            last_error=json.dumps(retry_metadata, ensure_ascii=False),
            helpdesk_ticket_id=ticket_id,
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
            onboarding_status=STATE_PAUSED_LICENSE_UNAVAILABLE,
            onboarding_complete=False,
            onboarding_completed_at=None,
        )
        log_warning(
            "Staff onboarding workflow paused due to license exhaustion",
            company_id=company_id,
            staff_id=staff_id,
            execution_id=execution_id,
            step=failed_step,
            helpdesk_ticket_id=ticket_id,
        )
        return {
            "state": STATE_PAUSED_LICENSE_UNAVAILABLE,
            "execution_id": execution_id,
            "retry_metadata": retry_metadata,
            "helpdesk_ticket_id": ticket_id,
        }
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
    policy = await workflow_repo.get_company_workflow_policy(
        company_id, default_workflow_key=_default_workflow_key(direction)
    )
    execution = await workflow_repo.create_or_reset_execution(
        company_id=company_id,
        staff_id=staff_id,
        workflow_key=str(policy.get("workflow_key") or _default_workflow_key(direction)),
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


async def process_paused_license_executions(*, limit: int = 20, company_id: int | None = None) -> dict[str, int]:
    resumed = 0
    skipped = 0
    while resumed < max(1, int(limit)):
        execution = await workflow_repo.claim_next_paused_license_execution(
            now_utc=_utc_now_naive(),
            company_id=company_id,
        )
        if not execution:
            break
        try:
            result = await resume_staff_onboarding_workflow_after_external_confirmation(
                company_id=int(execution["company_id"]),
                staff_id=int(execution["staff_id"]),
                execution_id=int(execution["id"]),
                initiated_by_user_id=None,
            )
            if result.get("state") == STATE_PAUSED_LICENSE_UNAVAILABLE:
                skipped += 1
            else:
                resumed += 1
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            log_error(
                "Failed resuming paused license-exhausted workflow execution",
                execution_id=execution.get("id"),
                company_id=execution.get("company_id"),
                staff_id=execution.get("staff_id"),
                error=str(exc),
            )
    return {"resumed": resumed, "skipped": skipped}


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
        "started_at": _serialise_dt(execution.get("started_at")),
        "completed_at": _serialise_dt(execution.get("completed_at")),
        "requested_at": _serialise_dt(execution.get("requested_at")),
        "scheduled_for_utc": _serialise_dt(execution.get("scheduled_for_utc")),
        "requested_timezone": execution.get("requested_timezone"),
    }


async def retry_failed_workflow_execution(
    *,
    execution_id: int,
    initiated_by_user_id: int | None,
) -> dict[str, Any]:
    """Reset a failed workflow execution and re-queue it for processing."""
    execution = await workflow_repo.get_execution_by_id(execution_id)
    if not execution:
        raise ValueError("Workflow execution not found")

    state = str(execution.get("state") or "").strip().lower()
    failed_states = {STATE_FAILED, STATE_OFFBOARDING_FAILED}
    if state not in failed_states:
        raise ValueError(f"Execution is not in a failed state (current: {state})")

    company_id = int(execution["company_id"])
    staff_id = int(execution["staff_id"])
    direction = str(execution.get("direction") or DIRECTION_ONBOARDING).strip().lower()
    if direction not in {DIRECTION_ONBOARDING, DIRECTION_OFFBOARDING}:
        direction = DIRECTION_ONBOARDING

    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise ValueError("Staff member not found")

    reset_status = STATE_OFFBOARDING_APPROVED if direction == DIRECTION_OFFBOARDING else STATE_APPROVED
    await staff_repo.reset_staff_onboarding_status(staff_id, onboarding_status=reset_status)

    await enqueue_staff_onboarding_workflow(
        company_id=company_id,
        staff_id=staff_id,
        initiated_by_user_id=initiated_by_user_id,
        direction=direction,
        requested_timezone=execution.get("requested_timezone"),
    )

    staff_name = f"{staff.get('first_name', '')} {staff.get('last_name', '')}".strip() or f"Staff #{staff_id}"
    await audit_service.log_action(
        user_id=initiated_by_user_id,
        action=f"staff.{direction}.workflow.retry",
        entity_type="staff",
        entity_id=staff_id,
        metadata={
            "company_id": company_id,
            "original_execution_id": execution_id,
            "direction": direction,
        },
    )
    log_info(
        "Retrying failed staff workflow execution",
        company_id=company_id,
        staff_id=staff_id,
        original_execution_id=execution_id,
        direction=direction,
        initiated_by_user_id=initiated_by_user_id,
    )
    return {
        "state": "queued",
        "direction": direction,
        "staff_id": staff_id,
        "staff_name": staff_name,
        "original_execution_id": execution_id,
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
