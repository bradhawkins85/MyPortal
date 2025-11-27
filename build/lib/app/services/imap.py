from __future__ import annotations

import base64
import email
import imaplib
import io
import json
import re
import secrets
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Any, Mapping

from app.core.database import db
from app.core.logging import log_error, log_info
from app.repositories import companies as company_repo
from app.repositories import imap_accounts as imap_repo
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.repositories import staff as staff_repo
from app.repositories import users as users_repo
from app.repositories import tickets as tickets_repo
from app.repositories import ticket_attachments as attachments_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import modules as modules_service
from app.services import system_state
from app.services import tickets as tickets_service
from app.services.sanitization import sanitize_rich_text
from app.services.scheduler import scheduler_service

_MAX_FETCH_BYTES = 5 * 1024 * 1024
_CID_REFERENCE_PATTERN = re.compile(r"(?i)cid:([^\"'>\s]+)")


def _redact_account(account: Mapping[str, Any]) -> dict[str, Any]:
    redacted = dict(account)
    redacted.pop("password_encrypted", None)
    return redacted


def _normalise_string(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or default
    return str(value).strip() or default


def _normalise_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _ticket_is_closed(ticket: Mapping[str, Any]) -> bool:
    """Return True when a ticket is in a terminal closed/resolved state."""

    status = str(ticket.get("status", "")).lower()
    return status in {"closed", "resolved"}


def _normalise_priority(value: Any, *, default: int = 100) -> int:
    if value in (None, ""):
        return default
    try:
        priority = int(value)
    except (TypeError, ValueError):
        raise ValueError("Priority must be a whole number")
    if priority < 0:
        raise ValueError("Priority must be zero or greater")
    return priority


_CONDITION_OPERATORS = {
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "matches",
    "not_matches",
    "in",
    "not_in",
    "present",
    "absent",
}
_REGEX_OPERATORS = {"matches", "not_matches"}
_SET_OPERATORS = {"in", "not_in"}
_BOOLEAN_OPERATORS = {"present", "absent"}


def _ensure_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _value_is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return any(_value_is_present(item) for item in value)
    return True


def _lookup_field(context: Mapping[str, Any], field: str) -> Any:
    if not field:
        return None
    parts = [part.strip().lower() for part in field.split(".") if part.strip()]
    if not parts:
        return None
    current: Any = context
    for part in parts:
        if isinstance(current, Mapping):
            if part in current:
                current = current[part]
            else:
                return None
        else:
            return None
    return current


def _normalise_value(value: Any, *, case_sensitive: bool) -> Any:
    if isinstance(value, str):
        return value if case_sensitive else value.lower()
    return value


def _validate_filter_node(node: Mapping[str, Any]) -> None:
    if "field" in node:
        if any(key in node for key in ("all", "any", "none")):
            raise ValueError("Filter conditions cannot contain nested groups")
        field = str(node.get("field") or "").strip()
        if not field:
            raise ValueError("Filter condition is missing a field name")
        operators = [op for op in _CONDITION_OPERATORS if op in node]
        if not operators:
            raise ValueError(f"Filter condition for '{field}' must define an operator")
        if len(operators) > 1:
            raise ValueError(f"Filter condition for '{field}' must define exactly one operator")
        operator = operators[0]
        value = node.get(operator)
        if operator in _BOOLEAN_OPERATORS:
            if value is None:
                pass
            elif not isinstance(value, bool):
                raise ValueError(f"Operator '{operator}' expects a boolean value")
        elif operator in _SET_OPERATORS:
            if not isinstance(value, list):
                raise ValueError(f"Operator '{operator}' expects an array of values")
        elif operator in _REGEX_OPERATORS:
            if not isinstance(value, str):
                raise ValueError(f"Operator '{operator}' expects a string pattern")
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"Invalid regular expression for '{field}': {exc}") from exc
        else:
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError(f"Operator '{operator}' has an unsupported value type")
        case_sensitive = node.get("case_sensitive")
        if case_sensitive is not None and not isinstance(case_sensitive, bool):
            raise ValueError("case_sensitive must be a boolean when provided")
        return

    has_group = False
    for key in ("all", "any", "none"):
        group = node.get(key)
        if group is None:
            continue
        has_group = True
        if not isinstance(group, list):
            raise ValueError(f"Filter group '{key}' must be an array")
        for child in group:
            if not isinstance(child, Mapping):
                raise ValueError("Filter group entries must be objects")
            _validate_filter_node(child)
    if not has_group:
        raise ValueError("Filter must define at least one condition")


def _validate_filter(filter_query: Mapping[str, Any]) -> None:
    _validate_filter_node(filter_query)


def _normalise_filter(value: Any) -> tuple[str | None, dict[str, Any] | None]:
    if value in (None, ""):
        return None, None
    parsed: Any
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None, None
        try:
            parsed = json.loads(trimmed)
        except json.JSONDecodeError as exc:  # pragma: no cover - json error messaging
            raise ValueError(
                f"Filter must be valid JSON (line {exc.lineno} column {exc.colno}): {exc.msg}"
            ) from exc
    elif isinstance(value, Mapping):
        parsed = dict(value)
    else:
        raise ValueError("Filter must be provided as JSON text or an object")
    if not isinstance(parsed, Mapping):
        raise ValueError("Filter must be a JSON object")
    parsed_mapping = dict(parsed)
    _validate_filter(parsed_mapping)
    canonical = json.dumps(parsed_mapping, separators=(",", ":"), sort_keys=True)
    return canonical, parsed_mapping


def _evaluate_condition(condition: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    field = str(condition.get("field") or "").strip()
    operator = next((op for op in _CONDITION_OPERATORS if op in condition), None)
    if operator is None:
        return True
    case_sensitive = bool(condition.get("case_sensitive", False))
    value = _lookup_field(context, field)
    values = _ensure_sequence(value)
    normalised_values = [
        _normalise_value(item, case_sensitive=case_sensitive) for item in values
    ]

    if operator == "equals":
        target = _normalise_value(condition.get("equals"), case_sensitive=case_sensitive)
        return any(item == target for item in normalised_values)
    if operator == "not_equals":
        target = _normalise_value(condition.get("not_equals"), case_sensitive=case_sensitive)
        return all(item != target for item in normalised_values)
    if operator == "contains":
        target = condition.get("contains")
        if isinstance(target, str):
            needle = target if case_sensitive else target.lower()
            for raw, norm in zip(values, normalised_values):
                if isinstance(raw, str) and isinstance(norm, str) and needle in norm:
                    return True
        else:
            target_norm = _normalise_value(target, case_sensitive=case_sensitive)
            return any(item == target_norm for item in normalised_values)
        return False
    if operator == "not_contains":
        target = condition.get("not_contains")
        if isinstance(target, str):
            needle = target if case_sensitive else target.lower()
            for raw, norm in zip(values, normalised_values):
                if isinstance(raw, str) and isinstance(norm, str) and needle in norm:
                    return False
            return True
        target_norm = _normalise_value(target, case_sensitive=case_sensitive)
        return all(item != target_norm for item in normalised_values)
    if operator == "starts_with":
        prefix = condition.get("starts_with")
        if not isinstance(prefix, str):
            return False
        prefix_norm = prefix if case_sensitive else prefix.lower()
        for raw, norm in zip(values, normalised_values):
            if isinstance(raw, str) and isinstance(norm, str) and norm.startswith(prefix_norm):
                return True
        return False
    if operator == "ends_with":
        suffix = condition.get("ends_with")
        if not isinstance(suffix, str):
            return False
        suffix_norm = suffix if case_sensitive else suffix.lower()
        for raw, norm in zip(values, normalised_values):
            if isinstance(raw, str) and isinstance(norm, str) and norm.endswith(suffix_norm):
                return True
        return False
    if operator in _REGEX_OPERATORS:
        pattern = condition.get(operator)
        if not isinstance(pattern, str):
            return False
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags)
        except re.error:
            return False
        matched = any(
            isinstance(raw, str) and compiled.search(raw) is not None for raw in values
        )
        return matched if operator == "matches" else not matched
    if operator in _SET_OPERATORS:
        raw_targets = condition.get(operator) or []
        target_values = {
            _normalise_value(item, case_sensitive=case_sensitive) for item in raw_targets
        }
        if operator == "in":
            return any(item in target_values for item in normalised_values)
        return all(item not in target_values for item in normalised_values)
    if operator == "present":
        requirement = condition.get("present")
        present = _value_is_present(value)
        return present if requirement is not False else not present
    if operator == "absent":
        requirement = condition.get("absent")
        present = _value_is_present(value)
        return (not present) if requirement is not False else present
    return True


def _evaluate_filter(rule: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    if "field" in rule:
        return _evaluate_condition(rule, context)
    all_group = rule.get("all")
    if all_group is not None:
        for child in all_group:
            if not _evaluate_filter(child, context):
                return False
    any_group = rule.get("any")
    if any_group is not None:
        if not any_group:
            return False
        if not any(_evaluate_filter(child, context) for child in any_group):
            return False
    none_group = rule.get("none")
    if none_group is not None:
        if any(_evaluate_filter(child, context) for child in none_group):
            return False
    return True


def _extract_domains(addresses: list[str]) -> list[str]:
    domains: list[str] = []
    for address in addresses:
        if "@" not in address:
            continue
        domain = address.rsplit("@", 1)[1].strip().lower()
        if not domain:
            continue
        domains.append(domain)
    return domains


def _build_filter_context(
    *,
    account: Mapping[str, Any],
    message: email.message.Message,
    subject: str,
    body: str,
    from_address: str,
    folder: str,
    flags: list[str],
    is_unread: bool,
    message_id: str,
) -> dict[str, Any]:
    from_addresses = _extract_email_addresses(from_address)
    from_domains = _extract_domains(from_addresses)
    to_addresses = _extract_email_addresses(message.get("To"))
    cc_addresses = _extract_email_addresses(message.get("Cc"))
    bcc_addresses = _extract_email_addresses(message.get("Bcc"))
    reply_to_addresses = _extract_email_addresses(message.get("Reply-To"))
    sender_addresses = _extract_email_addresses(message.get("Sender"))
    headers: dict[str, str] = {}
    for key, value in message.items():
        headers[key.lower()] = str(value)

    context: dict[str, Any] = {
        "account": {
            "id": account.get("id"),
            "name": _normalise_string(account.get("name")),
            "company_id": account.get("company_id"),
        },
        "mailbox": {
            "folder": folder,
            "name": _normalise_string(account.get("name")),
        },
        "subject": subject or "",
        "body": body or "",
        "message_id": message_id or "",
        "from": {
            "raw": from_address or "",
            "addresses": from_addresses,
            "domains": from_domains,
        },
        "reply_to": {
            "addresses": reply_to_addresses,
        },
        "sender": {
            "addresses": sender_addresses,
        },
        "to": to_addresses,
        "cc": cc_addresses,
        "bcc": bcc_addresses,
        "flags": [flag for flag in flags if flag],
        "is_unread": bool(is_unread),
        "is_read": not is_unread,
        "headers": headers,
    }
    if from_addresses:
        context["from"]["address"] = from_addresses[0]
    if from_domains:
        context["from"]["domain"] = from_domains[0]
    if to_addresses:
        context["to_domains"] = _extract_domains(to_addresses)
    if cc_addresses:
        context["cc_domains"] = _extract_domains(cc_addresses)
    return context


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_record_id(record: Any) -> int | None:
    if not record:
        return None
    if isinstance(record, Mapping):
        return _int_or_none(record.get("id"))
    if hasattr(record, "get"):
        try:
            return _int_or_none(record.get("id"))  # type: ignore[call-arg]
        except Exception:  # pragma: no cover - defensive
            pass
    try:
        return _int_or_none(record["id"])  # type: ignore[index]
    except Exception:  # pragma: no cover - defensive
        pass
    return _int_or_none(getattr(record, "id", None))


def _extract_email_addresses(from_header: str | None) -> list[str]:
    if not from_header:
        return []
    addresses = []
    for _name, email_address in getaddresses([from_header]):
        candidate = (email_address or "").strip()
        if candidate:
            addresses.append(candidate)
    return addresses


def _extract_message_ids(header_value: str | None) -> list[str]:
    """Extract message IDs from headers like In-Reply-To or References."""

    if not header_value:
        return []

    message_ids: list[str] = []

    for match in re.findall(r"<([^>]+)>", header_value):
        candidate = _normalise_string(match)
        if candidate:
            message_ids.append(candidate)

    if not message_ids:
        for part in header_value.split():
            candidate = _normalise_string(part)
            if candidate:
                message_ids.append(candidate)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_ids: list[str] = []
    for message_id in message_ids:
        lowered = message_id.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique_ids.append(message_id)

    return unique_ids


async def _resolve_ticket_entities(
    from_header: str | None,
    *,
    default_company_id: int | None = None,
) -> tuple[int | None, int | None]:
    email_addresses = _extract_email_addresses(from_header)
    seen_domains: set[str] = set()

    for email_address in email_addresses:
        if "@" not in email_address:
            continue
        domain = email_address.rsplit("@", 1)[1].lower()
        if not domain:
            continue
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        company = await company_repo.get_company_by_email_domain(domain)
        if not company:
            continue
        company_id = _int_or_none(company.get("id"))
        if company_id is None:
            continue
        user = await users_repo.get_user_by_email(email_address)
        requester_id = _extract_record_id(user)
        if requester_id is not None:
            return company_id, requester_id
        staff_member = await staff_repo.get_staff_by_company_and_email(company_id, email_address)
        if staff_member:
            return company_id, None
        return company_id, None

    company_id = _int_or_none(default_company_id)
    requester_id: int | None = None

    if company_id is not None:
        checked: set[str] = set()
        for email_address in email_addresses:
            if email_address in checked:
                continue
            checked.add(email_address)
            user = await users_repo.get_user_by_email(email_address)
            requester_id = _extract_record_id(user)
            if requester_id is not None:
                break
            staff_member = await staff_repo.get_staff_by_company_and_email(company_id, email_address)
            if staff_member:
                requester_id = None
                break

    return company_id, requester_id


async def _is_email_address_known(email_address: str) -> bool:
    """
    Check if an email address is known in the system.
    
    An email address is considered "known" if it exists in:
    - Users table
    - Staff table (any company)
    
    Args:
        email_address: Email address to check
    
    Returns:
        True if the email address is known, False otherwise
    """
    if not email_address or "@" not in email_address:
        return False
    
    # Check if email exists in users table
    user = await users_repo.get_user_by_email(email_address)
    if user:
        return True
    
    # Check if email exists in staff table (any company)
    staff_list = await staff_repo.list_staff_by_email(email_address)
    if staff_list:
        return True
    
    return False


async def _is_any_email_address_known(email_addresses: list[str]) -> bool:
    """
    Check if any of the provided email addresses is known in the system.
    
    This function optimizes the check by returning early on the first match,
    but still validates each address sequentially. In most cases, there will
    only be one sender address to check.
    
    Args:
        email_addresses: List of email addresses to check
    
    Returns:
        True if at least one email address is known, False otherwise
    """
    if not email_addresses:
        return False
    
    for email_addr in email_addresses:
        if await _is_email_address_known(email_addr):
            return True
    
    return False


async def list_accounts() -> list[dict[str, Any]]:
    accounts = await imap_repo.list_accounts()
    return [_redact_account(account) for account in accounts]


async def get_account(account_id: int, *, redact: bool = True) -> dict[str, Any] | None:
    account = await imap_repo.get_account(account_id)
    if not account:
        return None
    return _redact_account(account) if redact else account


async def _ensure_scheduled_task(account: Mapping[str, Any]) -> Mapping[str, Any]:
    account_id = account.get("id")
    if account_id is None:
        return account
    account_name = _normalise_string(account.get("name"), default=f"Mailbox {account_id}")
    company_id = account.get("company_id")
    active = bool(account.get("active", True))
    cron = _normalise_string(account.get("schedule_cron"), default="*/15 * * * *")
    username = _normalise_string(account.get("username"))
    host = _normalise_string(account.get("host"))
    description = f"Synchronise mailbox {username}@{host}" if username and host else "Synchronise mailbox"
    command = f"imap_sync:{account_id}"
    scheduled_task_id = account.get("scheduled_task_id")
    task = None
    if scheduled_task_id:
        task = await scheduled_tasks_repo.get_task(int(scheduled_task_id))
    if not task:
        task = await scheduled_tasks_repo.create_task(
            name=f"IMAP sync · {account_name}",
            command=command,
            cron=cron,
            company_id=int(company_id) if isinstance(company_id, int) else None,
            description=description,
            active=active,
        )
    else:
        task = await scheduled_tasks_repo.update_task(
            int(task["id"]),
            name=f"IMAP sync · {account_name}",
            command=command,
            cron=cron,
            company_id=int(company_id) if isinstance(company_id, int) else None,
            description=description,
            active=active,
            max_retries=int(task.get("max_retries") or 12),
            retry_backoff_seconds=int(task.get("retry_backoff_seconds") or 300),
        )
    if task:
        refreshed = await imap_repo.update_account(
            int(account_id),
            scheduled_task_id=int(task.get("id")) if task.get("id") is not None else None,
        )
        await scheduler_service.refresh()
        return refreshed or account
    await scheduler_service.refresh()
    return account


async def create_account(payload: Mapping[str, Any]) -> dict[str, Any]:
    name = _normalise_string(payload.get("name"), default="Mailbox")
    host = _normalise_string(payload.get("host"))
    port = int(payload.get("port") or 993)
    username = _normalise_string(payload.get("username"))
    password = _normalise_string(payload.get("password"))
    folder = _normalise_string(payload.get("folder"), default="INBOX")
    schedule_cron = _normalise_string(payload.get("schedule_cron"), default="*/15 * * * *")
    process_unread_only = _normalise_bool(payload.get("process_unread_only"), default=True)
    mark_as_read = _normalise_bool(payload.get("mark_as_read"), default=True)
    sync_known_only = _normalise_bool(payload.get("sync_known_only"), default=False)
    active = _normalise_bool(payload.get("active"), default=True)
    company_id = payload.get("company_id")
    priority = _normalise_priority(payload.get("priority"), default=100)
    filter_canonical, _ = _normalise_filter(payload.get("filter_query"))
    if not password:
        raise ValueError("Password is required")
    encrypted_password = encrypt_secret(password)
    account = await imap_repo.create_account(
        name=name,
        host=host,
        port=port,
        username=username,
        password_encrypted=encrypted_password,
        folder=folder or "INBOX",
        schedule_cron=schedule_cron,
        filter_query=filter_canonical,
        process_unread_only=process_unread_only,
        mark_as_read=mark_as_read,
        sync_known_only=sync_known_only,
        active=active,
        company_id=int(company_id) if isinstance(company_id, int) else None,
        priority=priority,
    )
    if not account:
        raise RuntimeError("Failed to create IMAP account")
    account = await _ensure_scheduled_task(account)
    return _redact_account(account)


async def update_account(account_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    existing = await imap_repo.get_account(account_id)
    if not existing:
        raise ValueError("Account not found")
    updates: dict[str, Any] = {}
    if "name" in payload:
        updates["name"] = _normalise_string(payload.get("name"), default=existing.get("name") or "Mailbox")
    if "host" in payload:
        updates["host"] = _normalise_string(payload.get("host"), default=existing.get("host") or "")
    if "port" in payload:
        try:
            updates["port"] = int(payload.get("port"))
        except (TypeError, ValueError):
            raise ValueError("Port must be a number")
    if "username" in payload:
        updates["username"] = _normalise_string(payload.get("username"), default=existing.get("username") or "")
    if "password" in payload:
        password = _normalise_string(payload.get("password"))
        if password:
            updates["password_encrypted"] = encrypt_secret(password)
    if "folder" in payload:
        updates["folder"] = _normalise_string(payload.get("folder"), default="INBOX")
    if "schedule_cron" in payload:
        updates["schedule_cron"] = _normalise_string(
            payload.get("schedule_cron"), default=existing.get("schedule_cron") or "*/15 * * * *"
        )
    if "filter_query" in payload:
        filter_canonical, _ = _normalise_filter(payload.get("filter_query"))
        updates["filter_query"] = filter_canonical
    if "process_unread_only" in payload:
        updates["process_unread_only"] = _normalise_bool(
            payload.get("process_unread_only"), default=existing.get("process_unread_only", True)
        )
    if "mark_as_read" in payload:
        updates["mark_as_read"] = _normalise_bool(
            payload.get("mark_as_read"), default=existing.get("mark_as_read", True)
        )
    if "sync_known_only" in payload:
        updates["sync_known_only"] = _normalise_bool(
            payload.get("sync_known_only"), default=existing.get("sync_known_only", False)
        )
    if "active" in payload:
        updates["active"] = _normalise_bool(payload.get("active"), default=existing.get("active", True))
    if "company_id" in payload:
        company_value = payload.get("company_id")
        if company_value in ("", None):
            updates["company_id"] = None
        else:
            try:
                updates["company_id"] = int(company_value)
            except (TypeError, ValueError):
                raise ValueError("Company must be numeric")
    if "priority" in payload:
        updates["priority"] = _normalise_priority(payload.get("priority"), default=existing.get("priority") or 100)
    updated = await imap_repo.update_account(account_id, **updates)
    if not updated:
        raise RuntimeError("Unable to update IMAP account")
    updated = await _ensure_scheduled_task(updated)
    return _redact_account(updated)


async def clone_account(account_id: int) -> dict[str, Any]:
    original = await imap_repo.get_account(account_id)
    if not original:
        raise LookupError("Account not found")
    password_encrypted = original.get("password_encrypted")
    if not password_encrypted:
        raise ValueError("Source account is missing credentials")

    base_name = _normalise_string(original.get("name"), default=f"Mailbox {account_id}")
    existing_accounts = await imap_repo.list_accounts()
    existing_names = {acc.get("name") for acc in existing_accounts if acc.get("name")}

    clone_name = f"{base_name} (copy)"
    suffix = 2
    while clone_name in existing_names:
        clone_name = f"{base_name} (copy {suffix})"
        suffix += 1

    priority_value = _normalise_priority(original.get("priority"), default=100)
    original_filter = original.get("filter_query")
    filter_canonical: str | None = None
    if isinstance(original_filter, Mapping):
        filter_canonical = json.dumps(dict(original_filter), separators=(",", ":"), sort_keys=True)
    elif isinstance(original_filter, str):
        filter_canonical = original_filter.strip() or None

    account = await imap_repo.create_account(
        name=clone_name,
        host=_normalise_string(original.get("host")),
        port=int(original.get("port") or 993),
        username=_normalise_string(original.get("username")),
        password_encrypted=password_encrypted,
        folder=_normalise_string(original.get("folder"), default="INBOX") or "INBOX",
        schedule_cron=_normalise_string(original.get("schedule_cron"), default="*/15 * * * *"),
        filter_query=filter_canonical,
        process_unread_only=bool(original.get("process_unread_only", True)),
        mark_as_read=bool(original.get("mark_as_read", True)),
        sync_known_only=bool(original.get("sync_known_only", False)),
        active=bool(original.get("active", True)),
        company_id=_int_or_none(original.get("company_id")),
        scheduled_task_id=None,
        priority=priority_value,
    )
    if not account:
        raise RuntimeError("Failed to clone IMAP account")
    account = await _ensure_scheduled_task(account)
    return _redact_account(account)


async def delete_account(account_id: int) -> None:
    existing = await imap_repo.get_account(account_id)
    if not existing:
        return
    scheduled_task_id = existing.get("scheduled_task_id")
    await imap_repo.delete_account(account_id)
    if scheduled_task_id:
        await scheduled_tasks_repo.delete_task(int(scheduled_task_id))
    await scheduler_service.refresh()


def _decode_subject(message: email.message.Message) -> str:
    raw = message.get("Subject", "")
    try:
        header = make_header(decode_header(raw))
        return str(header)
    except Exception:  # pragma: no cover - defensive decoding
        return raw


def _extract_body_and_attachments(message: email.message.Message) -> tuple[str, list[dict[str, Any]]]:
    """
    Extract body text and non-image attachments from an email message.
    
    Returns:
        Tuple of (body_html, attachments) where attachments is a list of dicts with:
        - filename: str
        - content_type: str
        - payload: bytes
    """
    def _decode_text_part(part: email.message.Message) -> str:
        payload = part.get_payload(decode=True) or b""
        if len(payload) > _MAX_FETCH_BYTES:
            payload = payload[: _MAX_FETCH_BYTES]
        try:
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace").strip()
        except LookupError:
            return payload.decode("utf-8", errors="replace").strip()

    if message.is_multipart():
        plain_parts: list[str] = []
        html_parts: list[str] = []
        inline_images: dict[str, tuple[str, bytes]] = {}
        attachments: list[dict[str, Any]] = []

        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            content_type = (part.get_content_type() or "").lower()
            disposition = (part.get_content_disposition() or "").lower()

            if content_type in {"text/plain", "text/html"} and disposition != "attachment":
                text = _decode_text_part(part)
                if not text:
                    continue
                if content_type == "text/plain":
                    plain_parts.append(text)
                else:
                    html_parts.append(text)
                continue

            content_id = part.get("Content-ID")
            
            # Handle inline images (embed as base64)
            if content_id and disposition != "attachment" and content_type.startswith("image/"):
                payload = part.get_payload(decode=True) or b""
                if payload and len(payload) <= _MAX_FETCH_BYTES:
                    normalised_id = content_id.strip().strip("<>").lower()
                    if normalised_id:
                        inline_images[normalised_id] = (content_type, payload)
                continue
            
            # Handle non-inline attachments (files to save)
            if disposition == "attachment" or (part.get_filename() and not content_id):
                payload = part.get_payload(decode=True) or b""
                if not payload or len(payload) > _MAX_FETCH_BYTES:
                    continue
                
                filename = part.get_filename()
                if filename:
                    # Decode filename if it's encoded
                    try:
                        decoded_header = make_header(decode_header(filename))
                        filename = str(decoded_header)
                    except Exception:
                        pass  # Use filename as-is if decoding fails
                else:
                    # Generate a filename if none provided
                    filename = f"attachment_{secrets.token_hex(4)}"
                
                attachments.append({
                    "filename": filename,
                    "content_type": content_type,
                    "payload": payload,
                })
                continue

        if html_parts:
            html_body = "\n\n".join(fragment for fragment in html_parts if fragment).strip()

            if inline_images and html_body:
                def _replace_cid(match: re.Match[str]) -> str:
                    cid_value = match.group(1)
                    if not cid_value:
                        return match.group(0)
                    lookup_key = cid_value.strip().strip("<>").lower()
                    resource = inline_images.get(lookup_key)
                    if not resource:
                        return match.group(0)
                    content_type, payload = resource
                    encoded = base64.b64encode(payload).decode("ascii")
                    return f"data:{content_type};base64,{encoded}"

                html_body = _CID_REFERENCE_PATTERN.sub(_replace_cid, html_body)

            return html_body, attachments

        if plain_parts:
            return "\n\n".join(fragment for fragment in plain_parts if fragment).strip(), attachments

        return "", attachments

    if message.get_content_type() == "text/html":
        return _decode_text_part(message), []
    if message.get_content_type() == "text/plain":
        return _decode_text_part(message), []

    payload = message.get_payload(decode=True) or b""
    if len(payload) > _MAX_FETCH_BYTES:
        payload = payload[: _MAX_FETCH_BYTES]
    try:
        return payload.decode(message.get_content_charset() or "utf-8", errors="replace").strip(), []
    except LookupError:
        return payload.decode("utf-8", errors="replace").strip(), []


def _extract_body(message: email.message.Message) -> str:
    """
    Extract body text from an email message (backward compatibility wrapper).
    
    Returns:
        Body HTML as a string
    """
    body, _ = _extract_body_and_attachments(message)
    return body


def _extract_ticket_number_from_subject(subject: str | None) -> str | None:
    """
    Extract ticket number from email subject.
    
    Looks for patterns like:
    - #123
    - Ticket #123
    - [Ticket #123]
    - RE: Support Request #123
    
    Args:
        subject: Email subject line
    
    Returns:
        Ticket number as string if found, None otherwise
    """
    if not subject:
        return None
    
    # Look for #number pattern (most common)
    match = re.search(r'#(\d+)', subject)
    if match:
        return match.group(1)
    
    # Look for "Ticket: number" or similar patterns
    match = re.search(r'[Tt]icket[:\s]+(\d+)', subject)
    if match:
        return match.group(1)
    
    return None


def _extract_syncro_message_id(text: str | None) -> str | None:
    """Extract a Syncro message identifier from email content.

    Syncro replies may include a marker such as ``(message id: 101748802)`` in
    the subject or body. The captured number can be used to match the ticket's
    ``external_reference`` field when the ticket originated from Syncro.

    Args:
        text: The subject or body text to search.

    Returns:
        The numeric message identifier as a string if present, otherwise None.
    """

    if not text:
        return None

    match = re.search(r"\(message id:\s*(\d+)\)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)

    return None


def _normalize_subject_for_matching(subject: str) -> str:
    """
    Normalize email subject for matching by removing common reply/forward prefixes.
    
    Removes prefixes like RE:, FW:, FWD:, etc. and strips whitespace.
    Also removes ticket numbers to match on the core subject.
    
    Args:
        subject: Email subject line
    
    Returns:
        Normalized subject string
    """
    if not subject:
        return ""
    
    normalized = subject.strip()
    
    # Remove common reply/forward prefixes (case insensitive, multiple levels)
    while True:
        original = normalized
        # Remove RE:, FW:, FWD:, etc. at the start
        normalized = re.sub(r'^(re|fw|fwd|fw|fwd):\s*', '', normalized, flags=re.IGNORECASE)
        # Remove [External] or similar tags
        normalized = re.sub(r'^\[.*?\]\s*', '', normalized)
        normalized = normalized.strip()
        if normalized == original:
            break
    
    # Remove ticket numbers
    normalized = re.sub(r'#\d+', '', normalized)
    normalized = re.sub(r'[Tt]icket[:\s]+\d+', '', normalized)
    
    # Clean up extra whitespace
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    
    return normalized


async def _find_existing_ticket_for_reply(
    subject: str,
    from_email: str,
    *,
    requester_id: int | None = None,
    related_message_ids: list[str] | None = None,
    message_body: str | None = None,
) -> dict[str, Any] | None:
    """
    Find an existing ticket that this email is likely a reply to.

    Priority order:
    1. Match In-Reply-To/References message IDs against ticket and reply external references
    2. Extract ticket number from subject
    3. Fuzzy subject match for non-closed tickets where sender is requester or watcher.

    Args:
        subject: Email subject line
        from_email: Sender email address
        requester_id: ID of the requester user if resolved
        related_message_ids: Message IDs from In-Reply-To/References headers
    
    Returns:
        Ticket record if found, None otherwise
    """
    related_ids = [message_id for message_id in related_message_ids or [] if message_id]

    if related_ids:
        from app.repositories.tickets import _normalise_ticket

        for message_id in related_ids:
            try:
                ticket = await tickets_repo.get_ticket_by_external_reference(message_id)
                if ticket and not _ticket_is_closed(ticket):
                    return ticket
            except Exception:  # pragma: no cover - defensive logging
                pass

            try:
                rows = await db.fetch_all(
                    """
                    SELECT t.*
                    FROM ticket_replies tr
                    JOIN tickets t ON tr.ticket_id = t.id
                    WHERE tr.external_reference = %s
                    LIMIT 1
                    """,
                    (message_id,),
                )
                if rows:
                    ticket = _normalise_ticket(rows[0])
                    if not _ticket_is_closed(ticket):
                        return ticket
            except Exception:  # pragma: no cover - defensive logging
                pass

    # Next, try to match Syncro-originated replies using the embedded message id
    syncro_external_id = _extract_syncro_message_id(subject) or _extract_syncro_message_id(message_body)
    if syncro_external_id:
        try:
            ticket = await tickets_repo.get_ticket_by_external_reference(syncro_external_id)
            if ticket and not _ticket_is_closed(ticket):
                return ticket
        except Exception:  # pragma: no cover - defensive logging
            pass

    # First, try to extract ticket number from subject
    ticket_number = _extract_ticket_number_from_subject(subject)
    if ticket_number:
        # Try to find ticket by ticket_number field
        try:
            rows = await db.fetch_all(
                "SELECT * FROM tickets WHERE ticket_number = %s LIMIT 1",
                (ticket_number,)
            )
            if rows:
                from app.repositories.tickets import _normalise_ticket
                ticket = _normalise_ticket(rows[0])
                # Don't match closed tickets - create a new ticket instead
                if _ticket_is_closed(ticket):
                    return None
                return ticket
        except Exception:  # pragma: no cover - defensive
            pass

        # Also try by ID if ticket_number is numeric
        try:
            ticket_id = int(ticket_number)
            rows = await db.fetch_all(
                "SELECT * FROM tickets WHERE id = %s LIMIT 1",
                (ticket_id,)
            )
            if rows:
                from app.repositories.tickets import _normalise_ticket
                ticket = _normalise_ticket(rows[0])
                # Don't match closed tickets - create a new ticket instead
                if _ticket_is_closed(ticket):
                    return None
                return ticket
        except (ValueError, Exception):  # pragma: no cover - defensive
            pass
    
    # If no ticket number found, try to match by normalized subject
    # Only match non-closed tickets where sender is requester or watcher
    normalized_subject = _normalize_subject_for_matching(subject)
    if not normalized_subject or len(normalized_subject) < 5:
        # Subject too short or empty to match reliably
        return None
    
    # Build query to find tickets with matching subject where sender is involved
    # We'll check if sender is the requester or a watcher
    try:
        # First, find tickets with similar subjects that are not closed/resolved
        # We'll use LIKE with wildcards to match normalized subjects
        query = """
            SELECT DISTINCT t.*
            FROM tickets t
            LEFT JOIN ticket_watchers tw ON t.id = tw.ticket_id
            LEFT JOIN users u ON tw.user_id = u.id
            WHERE t.status NOT IN ('closed', 'resolved')
        """
        params: list[Any] = []
        
        # If we have a requester_id, include tickets where they are the requester
        # or a watcher
        if requester_id is not None:
            query += """
                AND (t.requester_id = %s OR tw.user_id = %s)
            """
            params.extend([requester_id, requester_id])
        elif from_email:
            # If no requester_id but we have an email, check watchers by email
            query += """
                AND (
                    EXISTS (
                        SELECT 1 FROM users ru 
                        WHERE ru.id = t.requester_id AND LOWER(ru.email) = LOWER(%s)
                    )
                    OR LOWER(u.email) = LOWER(%s)
                )
            """
            params.extend([from_email, from_email])
        else:
            # No way to match sender, can't reliably determine if this is a reply
            return None
        
        query += " ORDER BY t.updated_at DESC LIMIT 20"
        
        rows = await db.fetch_all(query, tuple(params))
        
        if not rows:
            return None
        
        # Now check which tickets have matching normalized subjects
        from app.repositories.tickets import _normalise_ticket
        
        for row in rows:
            ticket = _normalise_ticket(row)
            ticket_subject = ticket.get("subject", "")
            ticket_normalized = _normalize_subject_for_matching(ticket_subject)
            
            # Check if normalized subjects match (case insensitive)
            if ticket_normalized.lower() == normalized_subject.lower():
                return ticket
        
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to search for existing ticket by subject",
            subject=subject,
            from_email=from_email,
            error=str(exc),
        )
    
    return None


async def _record_message(
    *,
    account_id: int,
    uid: str,
    status: str,
    ticket_id: int | None,
    error: str | None,
) -> None:
    await imap_repo.upsert_message(
        account_id=account_id,
        message_uid=uid,
        status=status,
        ticket_id=ticket_id,
        error=error,
        processed_at=datetime.now(timezone.utc),
    )


def _get_upload_directory() -> Path:
    """Get the base upload directory for ticket attachments."""
    base_dir = Path(__file__).parent.parent / "static" / "uploads" / "tickets"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _generate_secure_filename(original_filename: str) -> str:
    """Generate a secure filename using a random token."""
    # Extract extension from original filename
    extension = ""
    if "." in original_filename:
        extension = original_filename.rsplit(".", 1)[1].lower()
        # Limit extension length and sanitize
        extension = extension[:10]
        extension = "".join(c for c in extension if c.isalnum())
    
    # Generate random filename
    random_name = secrets.token_urlsafe(32)
    
    if extension:
        return f"{random_name}.{extension}"
    return random_name


async def _save_email_attachment(
    ticket_id: int,
    filename: str,
    content_type: str,
    payload: bytes,
) -> dict[str, Any] | None:
    """
    Save an email attachment to disk and create a database record.
    
    Args:
        ticket_id: The ticket ID to attach the file to
        filename: Original filename from the email
        content_type: MIME type of the attachment
        payload: File content as bytes
    
    Returns:
        The created attachment record or None if save failed
    """
    try:
        # Generate secure filename
        secure_filename = _generate_secure_filename(filename)
        
        # Get upload directory
        upload_dir = _get_upload_directory()
        file_path = upload_dir / secure_filename
        
        # Save file to disk
        file_size = len(payload)
        with open(file_path, "wb") as f:
            f.write(payload)
        
        log_info(
            f"Saved email attachment {secure_filename} ({file_size} bytes) for ticket {ticket_id}",
            ticket_id=ticket_id,
            original_filename=filename,
            secure_filename=secure_filename,
        )
        
        # Create database record with "restricted" access level
        attachment = await attachments_repo.create_attachment(
            ticket_id=ticket_id,
            filename=secure_filename,
            original_filename=filename,
            file_size=file_size,
            mime_type=content_type,
            access_level="restricted",
            uploaded_by_user_id=None,
        )
        return attachment
    except Exception as e:
        log_error(
            f"Failed to save email attachment: {e}",
            ticket_id=ticket_id,
            filename=filename,
            error=str(e),
        )
        # Clean up file if it was created
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass
        return None


async def sync_account(account_id: int) -> dict[str, Any]:
    if system_state.is_restart_pending():
        log_info(
            "Skipping IMAP sync because system restart is pending",
            account_id=account_id,
        )
        return {"status": "skipped", "reason": "pending_restart"}
    module = await modules_service.get_module("imap", redact=False)
    if not module or not module.get("enabled"):
        return {"status": "skipped", "reason": "Module disabled"}
    account = await imap_repo.get_account(account_id)
    if not account or not account.get("active", True):
        log_info("Skipping IMAP sync because account is inactive", account_id=account_id)
        return {"status": "skipped", "reason": "Account inactive"}
    encrypted_password = account.get("password_encrypted")
    if not encrypted_password:
        log_error("IMAP account missing credentials", account_id=account_id)
        return {"status": "error", "error": "Missing credentials"}
    try:
        password = decrypt_secret(encrypted_password)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Unable to decrypt IMAP credentials", account_id=account_id, error=str(exc))
        return {"status": "error", "error": "Unable to decrypt credentials"}

    host = _normalise_string(account.get("host"))
    port = int(account.get("port") or 993)
    username = _normalise_string(account.get("username"))
    folder = _normalise_string(account.get("folder"), default="INBOX") or "INBOX"
    process_unread_only = bool(account.get("process_unread_only", True))
    mark_as_read = bool(account.get("mark_as_read", True))

    mailbox: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None
    processed = 0
    errors: list[dict[str, Any]] = []

    try:
        if port == 993:
            mailbox = imaplib.IMAP4_SSL(host, port)
        else:
            mailbox = imaplib.IMAP4(host, port)
            try:
                mailbox.starttls()
            except Exception:
                pass
        mailbox.login(username, password)
        mailbox.select(folder, readonly=not mark_as_read)
        criterion = "UNSEEN" if process_unread_only else "ALL"
        result, data = mailbox.uid("search", None, criterion)
        if result != "OK" or not data:
            return {"status": "succeeded", "processed": 0, "errors": []}
        uids = data[0].split()
        for raw_uid in uids:
            uid = raw_uid.decode("utf-8", errors="ignore")
            if not uid:
                continue
            existing_message = await imap_repo.get_message(int(account_id), uid)
            if existing_message and existing_message.get("status") == "imported":
                continue
            # Use BODY.PEEK so that fetching the message does not set the \\Seen flag
            # before the ticket import succeeds.
            fetch_result, fetch_data = mailbox.uid("fetch", raw_uid, "(BODY.PEEK[] FLAGS)")
            if fetch_result != "OK" or not fetch_data:
                await _record_message(
                    account_id=int(account_id),
                    uid=uid,
                    status="error",
                    ticket_id=None,
                    error="Unable to fetch message",
                )
                errors.append({"uid": uid, "error": "Unable to fetch message"})
                continue
            message_bytes: bytes | None = None
            metadata_item: Any = None
            for item in fetch_data:
                if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                    metadata_item = item[0]
                    message_bytes = bytes(item[1])
                    break
            if message_bytes is None:
                await _record_message(
                    account_id=int(account_id),
                    uid=uid,
                    status="error",
                    ticket_id=None,
                    error="Unable to fetch message",
                )
                errors.append({"uid": uid, "error": "Unable to fetch message"})
                continue
            flags: list[str] = []
            if metadata_item is not None:
                if isinstance(metadata_item, (bytes, bytearray)):
                    metadata_bytes = bytes(metadata_item)
                else:
                    metadata_bytes = str(metadata_item).encode("utf-8", errors="ignore")
                try:
                    parsed_flags = imaplib.ParseFlags(metadata_bytes)
                except Exception:
                    parsed_flags = ()
                for flag in parsed_flags:
                    if isinstance(flag, bytes):
                        decoded = flag.decode("utf-8", errors="ignore")
                    else:
                        decoded = str(flag)
                    decoded = decoded.strip()
                    if decoded:
                        flags.append(decoded)
            message = email.message_from_bytes(message_bytes)
            subject = _decode_subject(message) or f"Email from {username}"
            body, email_attachments = _extract_body_and_attachments(message)
            message_id = _normalise_string(message.get("Message-ID"), default=uid)
            in_reply_to_ids = _extract_message_ids(_normalise_string(message.get("In-Reply-To")))
            reference_headers = message.get_all("References", [])
            reference_ids: list[str] = []
            for ref_header in reference_headers:
                reference_ids.extend(_extract_message_ids(ref_header))
            related_message_ids = in_reply_to_ids + reference_ids
            received_at: datetime | None = None
            raw_date = message.get("Date")
            if raw_date:
                try:
                    parsed_date = parsedate_to_datetime(raw_date)
                except (TypeError, ValueError, IndexError, OverflowError):
                    parsed_date = None
                if parsed_date is not None:
                    if parsed_date.tzinfo is None:
                        parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                    else:
                        parsed_date = parsed_date.astimezone(timezone.utc)
                    received_at = parsed_date
            from_address = _normalise_string(message.get("From"))
            normalised_flag_set = {flag.upper() for flag in flags}
            is_unread = "\\SEEN" not in normalised_flag_set if flags else True
            if process_unread_only:
                is_unread = True
            filter_rule = account.get("filter_query")
            if isinstance(filter_rule, Mapping) and filter_rule:
                context = _build_filter_context(
                    account=account,
                    message=message,
                    subject=subject,
                    body=body,
                    from_address=from_address,
                    folder=folder,
                    flags=flags,
                    is_unread=is_unread,
                    message_id=message_id,
                )
                if not _evaluate_filter(filter_rule, context):
                    log_info(
                        "Skipping IMAP message because it did not match the configured filter",
                        account_id=account_id,
                        uid=uid,
                    )
                    continue
            
            # Check if sync_known_only is enabled and filter by known email addresses
            sync_known_only = bool(account.get("sync_known_only", False))
            if sync_known_only:
                from_email_addresses = _extract_email_addresses(from_address)
                if not from_email_addresses:
                    log_info(
                        "Skipping IMAP message because no valid sender email address found",
                        account_id=account_id,
                        uid=uid,
                    )
                    continue
                
                # Check if any of the sender addresses are known
                if not await _is_any_email_address_known(from_email_addresses):
                    log_info(
                        "Skipping IMAP message from unknown sender",
                        account_id=account_id,
                        uid=uid,
                        from_address=from_address,
                    )
                    continue
            
            description_lines = []
            if from_address:
                description_lines.append(f"From: {from_address}")
            if body:
                description_lines.append("\n" + body)
            description = "\n\n".join(description_lines).strip()
            default_company_id = _int_or_none(account.get("company_id"))
            company_id, requester_id = await _resolve_ticket_entities(
                from_address,
                default_company_id=default_company_id,
            )
            
            # Check if this email is a reply to an existing ticket
            from_email = _extract_email_addresses(from_address)
            from_email_addr = from_email[0] if from_email else None
            existing_ticket = await _find_existing_ticket_for_reply(
                subject=subject,
                from_email=from_email_addr or "",
                requester_id=requester_id,
                related_message_ids=related_message_ids,
                message_body=body,
            )
            
            ticket: Mapping[str, Any] | None = None
            is_new_ticket = False
            
            try:
                if existing_ticket:
                    # This is a reply to an existing ticket
                    ticket = existing_ticket
                    ticket_id = ticket.get("id")
                    log_info(
                        "Email matched to existing ticket",
                        account_id=account_id,
                        uid=uid,
                        ticket_id=ticket_id,
                        subject=subject,
                    )
                else:
                    # Create a new ticket
                    ticket = await tickets_service.create_ticket(
                        subject=subject,
                        description=description or "Email body unavailable.",
                        requester_id=requester_id,
                        company_id=company_id,
                        assigned_user_id=None,
                        priority="normal",
                        status=None,
                        category="email",
                        module_slug="imap",
                        external_reference=message_id,
                    )
                    is_new_ticket = True
                    ticket_id = ticket.get("id") if isinstance(ticket, Mapping) else None
                    if ticket_id is not None:
                        try:
                            await tickets_service.refresh_ticket_ai_summary(int(ticket_id))
                        except RuntimeError:
                            pass
                        await tickets_service.refresh_ticket_ai_tags(int(ticket_id))
            except Exception as exc:  # pragma: no cover - defensive logging
                error_text = str(exc)
                errors.append({"uid": uid, "error": error_text})
                await _record_message(
                    account_id=int(account_id),
                    uid=uid,
                    status="error",
                    ticket_id=None,
                    error=error_text,
                )
                log_error(
                    "Failed to create ticket from IMAP message",
                    account_id=account_id,
                    uid=uid,
                    error=error_text,
                )
                continue
            ticket_id = ticket.get("id") if isinstance(ticket, Mapping) else None
            if isinstance(ticket_id, int):
                # For existing tickets (replies), always add a conversation entry
                # For new tickets, create_ticket already adds the initial reply
                if not is_new_ticket:
                    conversation_source = body or ""
                    sanitized = sanitize_rich_text(conversation_source)
                    if sanitized.has_rich_content:
                        reply_created_at = received_at or datetime.now(timezone.utc)
                        # Determine the author - use requester_id if available
                        reply_author_id = requester_id if requester_id is not None else None
                        try:
                            await tickets_repo.create_reply(
                                ticket_id=int(ticket_id),
                                author_id=reply_author_id,
                                body=sanitized.html,
                                is_internal=False,
                                external_reference=message_id if message_id else None,
                                created_at=reply_created_at,
                            )
                            log_info(
                                "Added email reply to existing ticket",
                                account_id=account_id,
                                uid=uid,
                                ticket_id=ticket_id,
                            )
                        except Exception as exc:  # pragma: no cover - defensive logging
                            log_error(
                                "Failed to add email reply to ticket",
                                account_id=account_id,
                                uid=uid,
                                ticket_id=ticket_id,
                                error=str(exc),
                            )
                
                # Save non-image attachments with restricted access
                for attachment_info in email_attachments:
                    try:
                        await _save_email_attachment(
                            ticket_id=int(ticket_id),
                            filename=attachment_info["filename"],
                            content_type=attachment_info["content_type"],
                            payload=attachment_info["payload"],
                        )
                    except Exception as exc:  # pragma: no cover - defensive logging
                        log_error(
                            "Failed to save email attachment",
                            account_id=account_id,
                            uid=uid,
                            ticket_id=ticket_id,
                            filename=attachment_info.get("filename"),
                            error=str(exc),
                        )
            await _record_message(
                account_id=int(account_id),
                uid=uid,
                status="imported",
                ticket_id=int(ticket_id) if isinstance(ticket_id, int) else None,
                error=None,
            )
            processed += 1
            if mark_as_read:
                try:
                    mailbox.uid("store", raw_uid, "+FLAGS", "(\\Seen)")
                except Exception:  # pragma: no cover - IMAP flag errors
                    log_error(
                        "Unable to mark message as read",
                        account_id=account_id,
                        uid=uid,
                    )
    except Exception as exc:  # pragma: no cover - network interaction
        log_error("IMAP synchronisation failed", account_id=account_id, error=str(exc))
        errors.append({"error": str(exc)})
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass
    await imap_repo.update_account(
        int(account_id),
        last_synced_at=datetime.now(timezone.utc),
    )
    log_info(
        "IMAP synchronisation completed",
        account_id=account_id,
        processed=processed,
        errors=len(errors),
    )
    status_value = "succeeded" if not errors else "completed_with_errors"
    return {"status": status_value, "processed": processed, "errors": errors}


async def sync_all_active() -> None:
    accounts = await imap_repo.list_accounts()
    sorted_accounts = sorted(
        accounts,
        key=lambda account: (
            int(account.get("priority") or 0),
            int(account.get("id") or 0),
        ),
    )
    for account in sorted_accounts:
        if not account.get("active", True):
            continue
        try:
            await sync_account(int(account["id"]))
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to synchronise IMAP account during bulk run",
                account_id=account.get("id"),
                error=str(exc),
            )
