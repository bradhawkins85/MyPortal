from __future__ import annotations

import email
import imaplib
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses
from typing import Any, Mapping

from app.core.logging import log_error, log_info
from app.repositories import imap_accounts as imap_repo
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.repositories import companies as company_repo
from app.repositories import staff as staff_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import modules as modules_service
from app.services import tickets as tickets_service
from app.services.scheduler import scheduler_service

_MAX_FETCH_BYTES = 5 * 1024 * 1024


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


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_email_addresses(from_header: str | None) -> list[str]:
    if not from_header:
        return []
    addresses = []
    for _name, email_address in getaddresses([from_header]):
        candidate = (email_address or "").strip()
        if candidate:
            addresses.append(candidate)
    return addresses


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
        staff_member = await staff_repo.get_staff_by_company_and_email(company_id, email_address)
        requester_id = _int_or_none(staff_member.get("id")) if staff_member else None
        return company_id, requester_id

    company_id = _int_or_none(default_company_id)
    requester_id: int | None = None

    if company_id is not None:
        checked: set[str] = set()
        for email_address in email_addresses:
            if email_address in checked:
                continue
            checked.add(email_address)
            staff_member = await staff_repo.get_staff_by_company_and_email(company_id, email_address)
            if staff_member:
                requester_id = _int_or_none(staff_member.get("id"))
                break

    return company_id, requester_id


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
    active = _normalise_bool(payload.get("active"), default=True)
    company_id = payload.get("company_id")
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
        process_unread_only=process_unread_only,
        mark_as_read=mark_as_read,
        active=active,
        company_id=int(company_id) if isinstance(company_id, int) else None,
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
    if "process_unread_only" in payload:
        updates["process_unread_only"] = _normalise_bool(
            payload.get("process_unread_only"), default=existing.get("process_unread_only", True)
        )
    if "mark_as_read" in payload:
        updates["mark_as_read"] = _normalise_bool(
            payload.get("mark_as_read"), default=existing.get("mark_as_read", True)
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
    updated = await imap_repo.update_account(account_id, **updates)
    if not updated:
        raise RuntimeError("Unable to update IMAP account")
    updated = await _ensure_scheduled_task(updated)
    return _redact_account(updated)


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


def _extract_body(message: email.message.Message) -> str:
    if message.is_multipart():
        parts: list[str] = []
        for part in message.walk():
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            if part.get_content_disposition() in {"attachment", "inline"}:
                continue
            payload = part.get_payload(decode=True) or b""
            if len(payload) > _MAX_FETCH_BYTES:
                payload = payload[: _MAX_FETCH_BYTES]
            try:
                text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
            parts.append(text)
        return "\n\n".join(parts).strip()
    payload = message.get_payload(decode=True) or b""
    if len(payload) > _MAX_FETCH_BYTES:
        payload = payload[: _MAX_FETCH_BYTES]
    try:
        return payload.decode(message.get_content_charset() or "utf-8", errors="replace").strip()
    except LookupError:
        return payload.decode("utf-8", errors="replace").strip()


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


async def sync_account(account_id: int) -> dict[str, Any]:
    module = await modules_service.get_module("imap", redact=False)
    if not module or not module.get("enabled"):
        log_info("Skipping IMAP sync because module is disabled", account_id=account_id)
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
            fetch_result, fetch_data = mailbox.uid("fetch", raw_uid, "(BODY.PEEK[])")
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
            raw_message = fetch_data[0][1]
            message = email.message_from_bytes(raw_message)
            subject = _decode_subject(message) or f"Email from {username}"
            body = _extract_body(message)
            message_id = _normalise_string(message.get("Message-ID"), default=uid)
            from_address = _normalise_string(message.get("From"))
            description_lines = []
            if from_address:
                description_lines.append(f"From: {from_address}")
            if message_id:
                description_lines.append(f"Message-ID: {message_id}")
            if body:
                description_lines.append("\n" + body)
            description = "\n\n".join(description_lines).strip()
            default_company_id = _int_or_none(account.get("company_id"))
            company_id, requester_id = await _resolve_ticket_entities(
                from_address,
                default_company_id=default_company_id,
            )
            try:
                ticket = await tickets_service.create_ticket(
                    subject=subject,
                    description=description or "Email body unavailable.",
                    requester_id=requester_id,
                    company_id=company_id,
                    assigned_user_id=None,
                    priority="normal",
                    status="open",
                    category="email",
                    module_slug="imap",
                    external_reference=message_id,
                )
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
    for account in accounts:
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
