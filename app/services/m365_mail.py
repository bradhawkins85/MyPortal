from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import quote, urlencode

import httpx

from app.core.database import db
from app.core.logging import log_error, log_info
from app.repositories import companies as company_repo
from app.repositories import m365 as m365_repo
from app.repositories import m365_mail_accounts as mail_repo
from app.repositories import scheduled_tasks as scheduled_tasks_repo
from app.repositories import tickets as tickets_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import m365 as m365_service
from app.services import modules as modules_service
from app.services import system_state
from app.services import tickets as tickets_service
from app.services.m365 import M365Error
from app.services.sanitization import sanitize_rich_text

# Reuse filter helpers from the IMAP module so we share the same filter DSL.
from app.services.imap import (
    _evaluate_filter,
    _extract_domains,
    _extract_email_addresses,
    _extract_message_ids,
    _find_existing_ticket_for_reply,
    _int_or_none,
    _is_any_email_address_known,
    _normalise_bool,
    _normalise_filter,
    _normalise_priority,
    _normalise_string,
    _resolve_ticket_entities,
    _save_email_attachment,
    _ticket_is_closed,
)

_MODULE_SLUG = "m365-mail"

_403_ERROR_MESSAGE = (
    "Mail sync failed (403 Forbidden). Access to the mailbox was denied. "
    "This may be because mailbox access is restricted by an Exchange Online "
    "policy, the mailbox does not exist, or the enterprise app has not been "
    "granted access. Verify that the enterprise app has Mail.ReadWrite "
    "application permission and that no Exchange Online access policies "
    "are blocking access, then retry the sync."
)

# Delegated OAuth scope for the per-account sign-in flow.  Mail.ReadWrite
# allows reading and marking messages as read.  offline_access provides the
# refresh_token we store for background syncs.
DELEGATED_MAIL_SCOPE = (
    "https://graph.microsoft.com/Mail.ReadWrite "
    "https://graph.microsoft.com/User.Read "
    "offline_access openid profile"
)


# ---------------------------------------------------------------------------
# Per-account delegated token helpers
# ---------------------------------------------------------------------------


def _account_has_delegated_tokens(account: Mapping[str, Any]) -> bool:
    """Return True when an account stores its own refresh token."""
    return bool(account.get("refresh_token"))


def account_auth_status(account: Mapping[str, Any]) -> str:
    """Return a human-readable auth status string for the account."""
    if _account_has_delegated_tokens(account):
        return "signed_in"
    if account.get("company_id"):
        return "company_credentials"
    return "not_configured"


def enrich_account_response(account: dict[str, Any]) -> dict[str, Any]:
    """Add computed fields before returning an account to the API/template."""
    account = dict(account)
    account["auth_status"] = account_auth_status(account)
    # Never leak tokens to the frontend
    account.pop("refresh_token", None)
    account.pop("access_token", None)
    return account


async def store_delegated_tokens(
    account_id: int,
    *,
    tenant_id: str,
    refresh_token: str,
    access_token: str,
    expires_at: datetime | None,
) -> dict[str, Any] | None:
    """Store encrypted delegated OAuth tokens on a mail account."""
    return await mail_repo.update_account_tokens(
        account_id,
        tenant_id=tenant_id,
        refresh_token=encrypt_secret(refresh_token),
        access_token=encrypt_secret(access_token),
        token_expires_at=expires_at,
    )


async def clear_delegated_tokens(account_id: int) -> dict[str, Any] | None:
    """Remove per-account delegated tokens (disconnect)."""
    return await mail_repo.clear_account_tokens(account_id)


async def _acquire_delegated_access_token(account: Mapping[str, Any]) -> str:
    """Acquire a Graph API access token using the account's own delegated tokens.

    If the cached access token is still valid it is returned immediately.
    Otherwise the stored refresh token is exchanged for a new access token at
    the Microsoft token endpoint.  The new tokens are persisted (encrypted)
    back to the database.

    Raises ``M365Error`` when the refresh token exchange fails.
    """
    account_id = int(account["id"])

    # Try the cached access token (5-minute safety margin)
    cached_token = account.get("access_token")
    expires_at = account.get("token_expires_at")
    if cached_token and expires_at:
        margin = datetime.now(timezone.utc) + timedelta(minutes=5)
        if expires_at > margin:
            return decrypt_secret(cached_token)

    refresh_token = account.get("refresh_token")
    if not refresh_token:
        raise M365Error("No delegated refresh token stored for this mail account")

    tenant_id = _normalise_string(account.get("tenant_id"))
    if not tenant_id:
        raise M365Error("No tenant ID stored for this mail account")

    decrypted_refresh = decrypt_secret(refresh_token)

    # Use the PKCE public client to exchange the refresh token — no
    # client_secret required (public client flow).
    token_endpoint = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    )
    data = {
        "client_id": await m365_service.get_effective_pkce_client_id(),
        "grant_type": "refresh_token",
        "refresh_token": decrypted_refresh,
        "scope": DELEGATED_MAIL_SCOPE,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(token_endpoint, data=data)

    if response.status_code != 200:
        log_error(
            "Failed to refresh delegated token for M365 mail account",
            account_id=account_id,
            status=response.status_code,
            body=response.text[:500] if response.text else "",
        )
        raise M365Error(
            "Unable to refresh delegated access token. "
            "The user may need to sign in again.",
            http_status=response.status_code,
        )

    payload = response.json()
    new_access = str(payload.get("access_token", ""))
    new_refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    new_expires: datetime | None = None
    if isinstance(expires_in, (int, float)):
        new_expires = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))

    # Persist the refreshed tokens.  When the token endpoint returns a new
    # refresh_token, encrypt and store it.  Otherwise keep the original.
    stored_refresh = encrypt_secret(str(new_refresh)) if new_refresh else refresh_token
    await mail_repo.update_account_tokens(
        account_id,
        tenant_id=tenant_id,
        refresh_token=stored_refresh,
        access_token=encrypt_secret(new_access),
        token_expires_at=new_expires,
    )

    return new_access


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------


async def list_accounts() -> list[dict[str, Any]]:
    accounts = await mail_repo.list_accounts()
    return [enrich_account_response(a) for a in accounts]


async def get_account(account_id: int) -> dict[str, Any] | None:
    account = await mail_repo.get_account(account_id)
    return enrich_account_response(account) if account else None


async def _ensure_scheduled_task(account: Mapping[str, Any]) -> Mapping[str, Any]:
    from app.services.scheduler import scheduler_service

    account_id = account.get("id")
    if account_id is None:
        return account
    account_name = _normalise_string(account.get("name"), default=f"Mailbox {account_id}")
    company_id = account.get("company_id")
    active = bool(account.get("active", True))
    cron = _normalise_string(account.get("schedule_cron"), default="*/15 * * * *")
    upn = _normalise_string(account.get("user_principal_name"))
    description = f"Synchronise O365 mailbox {upn}" if upn else "Synchronise O365 mailbox"
    command = f"m365_mail_sync:{account_id}"
    scheduled_task_id = account.get("scheduled_task_id")
    task = None
    if scheduled_task_id:
        task = await scheduled_tasks_repo.get_task(int(scheduled_task_id))
    if not task:
        task = await scheduled_tasks_repo.create_task(
            name=f"O365 mail sync · {account_name}",
            command=command,
            cron=cron,
            company_id=int(company_id) if isinstance(company_id, int) else None,
            description=description,
            active=active,
        )
    else:
        task = await scheduled_tasks_repo.update_task(
            int(task["id"]),
            name=f"O365 mail sync · {account_name}",
            command=command,
            cron=cron,
            company_id=int(company_id) if isinstance(company_id, int) else None,
            description=description,
            active=active,
            max_retries=int(task.get("max_retries") or 12),
            retry_backoff_seconds=int(task.get("retry_backoff_seconds") or 300),
        )
    if task:
        refreshed = await mail_repo.update_account(
            int(account_id),
            scheduled_task_id=int(task.get("id")) if task.get("id") is not None else None,
        )
        await scheduler_service.refresh()
        return refreshed or account
    await scheduler_service.refresh()
    return account


async def create_account(payload: Mapping[str, Any]) -> dict[str, Any]:
    name = _normalise_string(payload.get("name"), default="Mailbox")
    company_id = payload.get("company_id")
    if company_id is not None:
        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            raise ValueError("Company must be numeric")
    user_principal_name = _normalise_string(payload.get("user_principal_name"))
    if not user_principal_name:
        raise ValueError("User principal name (email) is required")
    mailbox_type = _normalise_string(payload.get("mailbox_type"), default="user")
    if mailbox_type not in ("user", "shared"):
        mailbox_type = "user"
    folder = _normalise_string(payload.get("folder"), default="Inbox")
    schedule_cron = _normalise_string(payload.get("schedule_cron"), default="*/15 * * * *")
    process_unread_only = _normalise_bool(payload.get("process_unread_only"), default=True)
    mark_as_read = _normalise_bool(payload.get("mark_as_read"), default=True)
    sync_known_only = _normalise_bool(payload.get("sync_known_only"), default=False)
    active = _normalise_bool(payload.get("active"), default=True)
    priority = _normalise_priority(payload.get("priority"), default=100)
    filter_canonical, _ = _normalise_filter(payload.get("filter_query"))

    account = await mail_repo.create_account(
        name=name,
        company_id=company_id,
        user_principal_name=user_principal_name,
        mailbox_type=mailbox_type,
        folder=folder or "Inbox",
        schedule_cron=schedule_cron,
        filter_query=filter_canonical,
        process_unread_only=process_unread_only,
        mark_as_read=mark_as_read,
        sync_known_only=sync_known_only,
        active=active,
        priority=priority,
    )
    if not account:
        raise RuntimeError("Failed to create Office 365 mail account")
    account = await _ensure_scheduled_task(account)
    try:
        await modules_service.update_module(_MODULE_SLUG, enabled=True)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to enable M365 mail module after account creation", error=str(exc))
    return enrich_account_response(account)


async def update_account(account_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    existing = await mail_repo.get_account(account_id)
    if not existing:
        raise ValueError("Account not found")
    updates: dict[str, Any] = {}
    if "name" in payload:
        updates["name"] = _normalise_string(payload.get("name"), default=existing.get("name") or "Mailbox")
    if "company_id" in payload:
        company_value = payload.get("company_id")
        if company_value in ("", None):
            updates["company_id"] = None
        else:
            try:
                updates["company_id"] = int(company_value)
            except (TypeError, ValueError):
                raise ValueError("Company must be numeric")
    if "user_principal_name" in payload:
        updates["user_principal_name"] = _normalise_string(
            payload.get("user_principal_name"),
            default=existing.get("user_principal_name") or "",
        )
    if "mailbox_type" in payload:
        mt = _normalise_string(payload.get("mailbox_type"), default="user")
        updates["mailbox_type"] = mt if mt in ("user", "shared") else "user"
    if "folder" in payload:
        updates["folder"] = _normalise_string(payload.get("folder"), default="Inbox")
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
    if "priority" in payload:
        updates["priority"] = _normalise_priority(payload.get("priority"), default=existing.get("priority") or 100)
    updated = await mail_repo.update_account(account_id, **updates)
    if not updated:
        raise RuntimeError("Unable to update Office 365 mail account")
    updated = await _ensure_scheduled_task(updated)
    return enrich_account_response(updated)


async def clone_account(account_id: int) -> dict[str, Any]:
    original = await mail_repo.get_account(account_id)
    if not original:
        raise LookupError("Account not found")

    base_name = _normalise_string(original.get("name"), default=f"Mailbox {account_id}")
    existing_accounts = await mail_repo.list_accounts()
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

    account = await mail_repo.create_account(
        name=clone_name,
        company_id=int(original.get("company_id")),
        user_principal_name=_normalise_string(original.get("user_principal_name")),
        mailbox_type=_normalise_string(original.get("mailbox_type"), default="user"),
        folder=_normalise_string(original.get("folder"), default="Inbox") or "Inbox",
        schedule_cron=_normalise_string(original.get("schedule_cron"), default="*/15 * * * *"),
        filter_query=filter_canonical,
        process_unread_only=bool(original.get("process_unread_only", True)),
        mark_as_read=bool(original.get("mark_as_read", True)),
        sync_known_only=bool(original.get("sync_known_only", False)),
        active=bool(original.get("active", True)),
        scheduled_task_id=None,
        priority=priority_value,
    )
    if not account:
        raise RuntimeError("Failed to clone Office 365 mail account")
    account = await _ensure_scheduled_task(account)
    return enrich_account_response(account)


async def delete_account(account_id: int) -> None:
    from app.services.scheduler import scheduler_service

    existing = await mail_repo.get_account(account_id)
    if not existing:
        return
    scheduled_task_id = existing.get("scheduled_task_id")
    await mail_repo.delete_account(account_id)
    if scheduled_task_id:
        await scheduled_tasks_repo.delete_task(int(scheduled_task_id))
    await scheduler_service.refresh()


# ---------------------------------------------------------------------------
# Record message helper
# ---------------------------------------------------------------------------


async def _record_message(
    *,
    account_id: int,
    uid: str,
    status: str,
    ticket_id: int | None,
    error: str | None,
) -> None:
    try:
        await mail_repo.upsert_message(
            account_id=account_id,
            message_uid=uid,
            status=status,
            ticket_id=ticket_id,
            error=error,
            processed_at=datetime.now(timezone.utc),
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to record M365 mail message status",
            account_id=account_id,
            message_uid=uid,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Graph API helpers for mailbox email access
# ---------------------------------------------------------------------------

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_MIN_FOLDER_ID_LENGTH = 20
_WELL_KNOWN_MAIL_FOLDERS = {
    "archive",
    "clutter",
    "conflicts",
    "conversationhistory",
    "deleteditems",
    "drafts",
    "inbox",
    "junkemail",
    "localfailures",
    "outbox",
    "recoverableitemsdeletions",
    "recoverableitemsversions",
    "scheduled",
    "searchfolders",
    "sentitems",
    "serverfailures",
    "syncissues",
}


async def _graph_get(access_token: str, url: str) -> dict[str, Any]:
    """Perform a GET request to Microsoft Graph."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=headers)
    if response.status_code != 200:
        log_error(
            "Microsoft Graph mail request failed",
            url=url,
            status=response.status_code,
            body=response.text[:500] if response.text else "",
        )
        raise M365Error(
            f"Microsoft Graph request failed ({response.status_code})",
            http_status=response.status_code,
        )
    return response.json()


async def _graph_get_bytes(access_token: str, url: str) -> bytes:
    """Perform a GET request to Microsoft Graph and return raw bytes."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=headers)
    if response.status_code != 200:
        log_error(
            "Microsoft Graph mail binary request failed",
            url=url,
            status=response.status_code,
            body=response.text[:500] if response.text else "",
        )
        raise M365Error(
            f"Microsoft Graph binary request failed ({response.status_code})",
            http_status=response.status_code,
        )
    return response.content


async def _graph_patch(access_token: str, url: str, payload: dict[str, Any]) -> None:
    """Perform a PATCH request to Microsoft Graph."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.patch(url, headers=headers, json=payload)
    if response.status_code not in (200, 204):
        log_error(
            "Microsoft Graph PATCH failed",
            url=url,
            status=response.status_code,
        )


def _looks_like_graph_folder_id(folder: str) -> bool:
    """Heuristic check for Graph folder IDs.

    Graph uses opaque, base64-like IDs (often starting with AAMk/AQMk). Treat
    any long, space-free token as an ID to avoid unnecessary display-name
    lookups and to keep working if Microsoft introduces new prefixes.
    """
    normalized = (folder or "").strip()
    if not normalized:
        return False  # Whitespace-only input is not a valid folder ID
    # Spaces imply user-facing names (not opaque IDs).
    if " " in normalized:
        return False
    if normalized.startswith(("AAMk", "AQMk")):
        return True  # Current Graph folder ID prefixes
    return len(normalized) >= _MIN_FOLDER_ID_LENGTH  # Fallback for future prefixes


def _escape_odata_string(value: str) -> str:
    """Escape a string literal for use in an OData filter expression.

    Currently handles the required single-quote doubling for string literals.
    Extend this if additional OData escaping is needed in the future.
    """
    return value.replace("'", "''")


async def _resolve_mail_folder_identifier(
    *,
    access_token: str,
    upn: str,
    folder: str,
) -> str:
    """Resolve a mailbox folder display name to a Graph folder ID when needed."""
    folder_path = (folder or "").strip()

    async def _resolve_top_level(folder_name: str) -> str:
        if folder_name.lower() in _WELL_KNOWN_MAIL_FOLDERS or _looks_like_graph_folder_id(folder_name):
            return folder_name

        # OData string literal escaping (single-quote doubling); urlencode then handles
        # URL encoding of the full filter expression.
        filter_value = _escape_odata_string(folder_name)
        params = {
            "$filter": f"displayName eq '{filter_value}'",
            "$select": "id,displayName",
            "$top": "1",
        }
        url = f"{_GRAPH_BASE}/users/{quote(upn, safe='')}/mailFolders?" + urlencode(
            params,
            quote_via=quote,  # Match path encoding (spaces as %20) for consistent OData queries
            safe="$,",  # Keep $ for OData operators and commas for $select field lists; encode all other characters
        )
        data = await _graph_get(access_token, url)
        folders = data.get("value") or []
        if folders:
            folder_id = folders[0].get("id")
            if folder_id:
                return folder_id

        raise M365Error(f"Mail folder '{folder_name}' not found or inaccessible", http_status=404)

    async def _resolve_child_folder(parent_identifier: str, child_name: str) -> str:
        if _looks_like_graph_folder_id(child_name):
            return child_name

        filter_value = _escape_odata_string(child_name)
        params = {
            "$filter": f"displayName eq '{filter_value}'",
            "$select": "id,displayName",
            "$top": "1",
        }
        url = (
            f"{_GRAPH_BASE}/users/{quote(upn, safe='')}/mailFolders/{quote(parent_identifier, safe='')}/childFolders?"
            + urlencode(
                params,
                quote_via=quote,
                safe="$,",
            )
        )
        data = await _graph_get(access_token, url)
        child_folders = data.get("value") or []
        if not child_folders:
            raise M365Error(
                f"Mail folder '{folder_path}' not found or inaccessible",
                http_status=404,
            )
        folder_id = child_folders[0].get("id")
        if not folder_id:
            raise M365Error(
                f"Mail folder '{folder_path}' found but missing folder ID",
                http_status=404,
            )
        return folder_id

    if folder_path.startswith("/") or folder_path.endswith("/"):
        raise M365Error(
            f"Mail folder path '{folder_path}' cannot start or end with '/'",
            http_status=400,
        )

    segments = folder_path.split("/")
    if any(not seg for seg in segments):
        raise M365Error(
            f"Mail folder path '{folder_path}' contains empty segments; use 'Parent/Subfolder' format",
            http_status=400,
        )
    if len(segments) > 1:
        # Resolve the first segment against the root, then walk child folders for the rest
        parent_identifier = await _resolve_top_level(segments[0])
        for child_name in segments[1:]:
            parent_identifier = await _resolve_child_folder(parent_identifier, child_name)

        return parent_identifier

    return await _resolve_top_level(folder_path)


def _build_filter_context(
    *,
    account: Mapping[str, Any],
    graph_message: Mapping[str, Any],
    subject: str,
    body: str,
    from_address: str,
    folder: str,
    is_unread: bool,
    message_id: str,
) -> dict[str, Any]:
    """Build a filter evaluation context from a Graph API message object.

    Mirrors the fields available in the IMAP filter context so that the same
    filter DSL works for both IMAP and Office 365 mailboxes.
    """
    from_addresses = _extract_email_addresses(from_address)
    from_domains = _extract_domains(from_addresses)

    to_recipients = graph_message.get("toRecipients") or []
    cc_recipients = graph_message.get("ccRecipients") or []
    bcc_recipients = graph_message.get("bccRecipients") or []
    reply_to_list = graph_message.get("replyTo") or []

    def _extract_recipient_addresses(recipients: list[dict[str, Any]]) -> list[str]:
        addresses: list[str] = []
        for r in recipients:
            email_addr = r.get("emailAddress", {})
            addr = (email_addr.get("address") or "").strip()
            if addr:
                addresses.append(addr)
        return addresses

    to_addresses = _extract_recipient_addresses(to_recipients)
    cc_addresses = _extract_recipient_addresses(cc_recipients)
    bcc_addresses = _extract_recipient_addresses(bcc_recipients)
    reply_to_addresses = _extract_recipient_addresses(reply_to_list)

    # Build headers dict from internetMessageHeaders if available
    headers: dict[str, str] = {}
    for header in (graph_message.get("internetMessageHeaders") or []):
        hdr_name = (header.get("name") or "").lower()
        if hdr_name:
            headers[hdr_name] = header.get("value") or ""

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
            "addresses": from_addresses,
        },
        "to": to_addresses,
        "cc": cc_addresses,
        "bcc": bcc_addresses,
        "flags": [],
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


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------


async def sync_account(account_id: int) -> dict[str, Any]:
    """Synchronise a single Office 365 mailbox via Microsoft Graph API."""

    if system_state.is_restart_pending():
        log_info(
            "Skipping M365 mail sync because system restart is pending",
            account_id=account_id,
        )
        return {"status": "skipped", "reason": "pending_restart"}

    module = await modules_service.get_module(_MODULE_SLUG, redact=False)
    if not module or not module.get("enabled"):
        return {"status": "skipped", "reason": "Module disabled"}

    account = await mail_repo.get_account(account_id)
    if not account or not account.get("active", True):
        log_info("Skipping M365 mail sync because account is inactive", account_id=account_id)
        return {"status": "skipped", "reason": "Account inactive"}

    company_id = account.get("company_id")
    auth_company_id = _int_or_none(company_id)

    upn = _normalise_string(account.get("user_principal_name"))
    if not upn:
        return {"status": "error", "error": "User principal name not configured"}

    folder = _normalise_string(account.get("folder"), default="Inbox") or "Inbox"
    process_unread_only = bool(account.get("process_unread_only", True))
    mark_as_read = bool(account.get("mark_as_read", True))

    # Per-account delegated tokens take priority over company credentials.
    # When the admin has signed in directly for this mailbox, the account
    # stores its own refresh_token and we use that instead.
    using_delegated = _account_has_delegated_tokens(account)

    if using_delegated:
        try:
            access_token = await _acquire_delegated_access_token(account)
        except Exception as exc:
            log_error(
                "Unable to acquire delegated access token for mail sync",
                account_id=account_id,
                error=str(exc),
            )
            return {
                "status": "error",
                "error": (
                    "Unable to authenticate with the signed-in user credentials. "
                    "The user may need to sign in again."
                ),
            }
    else:
        # Fall back to company credentials (per-tenant enterprise app)
        if auth_company_id is None:
            provisioned = await m365_repo.list_provisioned_company_ids()
            if provisioned:
                auth_company_id = min(provisioned)
            else:
                return {"status": "error", "error": "No Microsoft 365 credentials configured. Please sign in to authorize access to the mailbox."}

        try:
            access_token = await m365_service.acquire_access_token(
                int(auth_company_id), force_client_credentials=True
            )
        except Exception as exc:
            log_error(
                "Unable to acquire M365 access token for mail sync",
                account_id=account_id,
                company_id=auth_company_id,
                error=str(exc),
            )
            return {"status": "error", "error": f"Unable to authenticate with Microsoft 365: {exc}"}

    processed = 0
    errors: list[dict[str, Any]] = []

    try:
        # Build the messages URL
        # For both user and shared mailboxes the Graph API uses /users/{upn}/...
        folder_identifier = await _resolve_mail_folder_identifier(
            access_token=access_token,
            upn=upn,
            folder=folder,
        )
        messages_url = f"{_GRAPH_BASE}/users/{quote(upn, safe='')}/mailFolders/{quote(folder_identifier, safe='')}/messages"
        query_params = {
            "$top": "50",
            "$orderby": "receivedDateTime asc",
            "$select": (
                "id,subject,body,from,toRecipients,ccRecipients,bccRecipients,"
                "replyTo,internetMessageHeaders,internetMessageId,isRead,receivedDateTime,"
                "hasAttachments,conversationId"
            ),
        }
        # Note: $filter is intentionally omitted. Combining $filter=isRead eq false
        # with $orderby on the messages endpoint returns ErrorAccessDenied (403) for
        # shared mailboxes and service accounts in certain Exchange Online configurations.
        # Unread filtering is applied client-side using the isRead field from $select.
        full_url = messages_url + "?" + urlencode(query_params, quote_via=quote, safe="$,")

        # Paginate through all messages
        delegated_fallback_attempted = False
        while full_url:
            try:
                data = await _graph_get(access_token, full_url)
            except M365Error as exc:
                if exc.http_status == 403 and using_delegated and not delegated_fallback_attempted:
                    # Delegated token lacks access to this mailbox.
                    # Fall back to client_credentials (app-level permissions)
                    # which can access any mailbox in the tenant.
                    delegated_fallback_attempted = True
                    log_info(
                        "Delegated token got 403; falling back to client_credentials",
                        account_id=account_id,
                        upn=upn,
                    )
                    if auth_company_id is None:
                        provisioned = await m365_repo.list_provisioned_company_ids()
                        if provisioned:
                            auth_company_id = min(provisioned)
                    if auth_company_id is not None:
                        try:
                            access_token = await m365_service.acquire_access_token(
                                int(auth_company_id), force_client_credentials=True
                            )
                            using_delegated = False
                            continue
                        except Exception as fb_exc:
                            log_error(
                                "Failed to acquire client_credentials token for delegated fallback",
                                account_id=account_id,
                                error=str(fb_exc),
                            )
                    errors.append({
                        "error": (
                            "Mail sync failed (403 Forbidden). The signed-in user "
                            "may not have access to this mailbox and no company "
                            "credentials are available to fall back to. Please sign "
                            "in again with a user that has access, or configure "
                            "Microsoft 365 company credentials."
                        )
                    })
                    break
                if exc.http_status == 403 and not using_delegated:
                    log_error(
                        "Failed to fetch messages from M365 mailbox",
                        account_id=account_id,
                        upn=upn,
                        error=str(exc),
                    )
                    errors.append({"error": _403_ERROR_MESSAGE})
                    break
                if exc.http_status == 403:
                    errors.append({"error": _403_ERROR_MESSAGE})
                    break
                log_error(
                    "Failed to fetch messages from M365 mailbox",
                    account_id=account_id,
                    upn=upn,
                    error=str(exc),
                )
                errors.append({"error": f"Failed to fetch messages: {exc}"})
                break
            except Exception as exc:
                log_error(
                    "Failed to fetch messages from M365 mailbox",
                    account_id=account_id,
                    upn=upn,
                    error=str(exc),
                )
                errors.append({"error": f"Failed to fetch messages: {exc}"})
                break

            messages = data.get("value") or []
            full_url = data.get("@odata.nextLink")

            for msg in messages:
                msg_id = msg.get("id") or ""
                internet_msg_id = msg.get("internetMessageId") or msg_id

                if not msg_id:
                    continue

                # Skip already-read messages when only processing unread
                if process_unread_only and msg.get("isRead", False):
                    continue

                # Check if already processed
                existing_message = await mail_repo.get_message(int(account_id), msg_id)
                if existing_message and existing_message.get("status") == "imported":
                    continue

                # Extract message details
                subject = msg.get("subject") or f"Email from {upn}"
                body_content = msg.get("body", {})
                body = body_content.get("content") or ""

                from_data = msg.get("from", {})
                from_email_data = from_data.get("emailAddress", {})
                from_address = from_email_data.get("address") or ""
                from_name = from_email_data.get("name") or ""
                from_header = f"{from_name} <{from_address}>" if from_name and from_address else from_address

                is_unread = not msg.get("isRead", False)

                # Parse received date
                received_at: datetime | None = None
                raw_received = msg.get("receivedDateTime")
                if raw_received:
                    try:
                        parsed_date = datetime.fromisoformat(raw_received.replace("Z", "+00:00"))
                        received_at = parsed_date.astimezone(timezone.utc)
                    except (TypeError, ValueError):
                        pass

                # Extract In-Reply-To and References from internet message headers
                in_reply_to_ids: list[str] = []
                reference_ids: list[str] = []
                for header in (msg.get("internetMessageHeaders") or []):
                    hdr_name = (header.get("name") or "").lower()
                    hdr_value = header.get("value") or ""
                    if hdr_name == "in-reply-to":
                        in_reply_to_ids.extend(_extract_message_ids(hdr_value))
                    elif hdr_name == "references":
                        reference_ids.extend(_extract_message_ids(hdr_value))
                related_message_ids = in_reply_to_ids + reference_ids

                # Apply filter rules
                filter_rule = account.get("filter_query")
                if isinstance(filter_rule, Mapping) and filter_rule:
                    context = _build_filter_context(
                        account=account,
                        graph_message=msg,
                        subject=subject,
                        body=body,
                        from_address=from_header,
                        folder=folder,
                        is_unread=is_unread,
                        message_id=internet_msg_id,
                    )
                    if not _evaluate_filter(filter_rule, context):
                        log_info(
                            "Skipping M365 message because it did not match the configured filter",
                            account_id=account_id,
                            message_id=msg_id,
                        )
                        continue

                # Check sync_known_only
                sync_known_only = bool(account.get("sync_known_only", False))
                if sync_known_only:
                    from_email_addresses = _extract_email_addresses(from_header)
                    if not from_email_addresses:
                        log_info(
                            "Skipping M365 message because no valid sender email address found",
                            account_id=account_id,
                            message_id=msg_id,
                        )
                        continue
                    if not await _is_any_email_address_known(from_email_addresses):
                        log_info(
                            "Skipping M365 message from unknown sender",
                            account_id=account_id,
                            message_id=msg_id,
                            from_address=from_address,
                        )
                        continue

                # Resolve ticket entities
                description_lines = []
                if from_header:
                    description_lines.append(f"From: {from_header}")
                if body:
                    description_lines.append("\n" + body)
                description = "\n\n".join(description_lines).strip()
                default_company_id = _int_or_none(account.get("company_id"))
                ticket_company_id, requester_id = await _resolve_ticket_entities(
                    from_header,
                    default_company_id=default_company_id,
                )

                # Find existing ticket for reply
                from_email_addr = from_address if from_address else None
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
                        ticket = existing_ticket
                        ticket_id = ticket.get("id")
                        log_info(
                            "M365 email matched to existing ticket",
                            account_id=account_id,
                            message_id=msg_id,
                            ticket_id=ticket_id,
                            subject=subject,
                        )
                    else:
                        ticket = await tickets_service.create_ticket(
                            subject=subject,
                            description=description or "Email body unavailable.",
                            requester_id=requester_id,
                            company_id=ticket_company_id,
                            assigned_user_id=None,
                            priority="normal",
                            status=None,
                            category="email",
                            module_slug=_MODULE_SLUG,
                            external_reference=internet_msg_id,
                            requester_email=from_email_addr if requester_id is None else None,
                        )
                        is_new_ticket = True
                        ticket_id = ticket.get("id") if isinstance(ticket, Mapping) else None
                        if ticket_id is not None:
                            try:
                                await tickets_service.refresh_ticket_ai_summary(int(ticket_id))
                            except RuntimeError:
                                pass
                            try:
                                await tickets_service.refresh_ticket_ai_tags(int(ticket_id))
                            except Exception:
                                pass
                except Exception as exc:  # pragma: no cover - defensive logging
                    error_text = str(exc)
                    errors.append({"message_id": msg_id, "error": error_text})
                    await _record_message(
                        account_id=int(account_id),
                        uid=msg_id,
                        status="error",
                        ticket_id=None,
                        error=error_text,
                    )
                    log_error(
                        "Failed to create ticket from M365 message",
                        account_id=account_id,
                        message_id=msg_id,
                        error=error_text,
                    )
                    continue

                ticket_id = ticket.get("id") if isinstance(ticket, Mapping) else None
                if isinstance(ticket_id, int):
                    # Add reply to existing ticket
                    if not is_new_ticket:
                        conversation_source = body or ""
                        sanitized = sanitize_rich_text(conversation_source)
                        if sanitized.has_rich_content:
                            reply_created_at = received_at or datetime.now(timezone.utc)
                            reply_author_id = requester_id if requester_id is not None else None
                            reply_added = False
                            try:
                                await tickets_repo.create_reply(
                                    ticket_id=int(ticket_id),
                                    author_id=reply_author_id,
                                    body=sanitized.html,
                                    is_internal=False,
                                    external_reference=internet_msg_id if internet_msg_id else None,
                                    created_at=reply_created_at,
                                )
                                reply_added = True
                                log_info(
                                    "Added M365 email reply to existing ticket",
                                    account_id=account_id,
                                    message_id=msg_id,
                                    ticket_id=ticket_id,
                                )
                            except Exception as exc:  # pragma: no cover - defensive logging
                                log_error(
                                    "Failed to add M365 email reply to ticket",
                                    account_id=account_id,
                                    message_id=msg_id,
                                    ticket_id=ticket_id,
                                    error=str(exc),
                                )

                            # Trigger ticket updated event for email replies
                            if reply_added:
                                try:
                                    actor_info: dict[str, Any] | None = None
                                    if reply_author_id is not None:
                                        actor_info = {"id": reply_author_id}
                                    await tickets_service.emit_ticket_updated_event(
                                        ticket_id,
                                        actor=actor_info,
                                    )
                                except Exception as exc:  # pragma: no cover - defensive logging
                                    log_error(
                                        "Failed to trigger automation for M365 email reply",
                                        account_id=account_id,
                                        message_id=msg_id,
                                        ticket_id=ticket_id,
                                        error=str(exc),
                                    )

                    # Fetch and save attachments if any
                    if msg.get("hasAttachments"):
                        try:
                            await _save_graph_attachments(
                                access_token=access_token,
                                upn=upn,
                                message_id=msg_id,
                                ticket_id=int(ticket_id),
                            )
                        except Exception as exc:  # pragma: no cover - defensive logging
                            log_error(
                                "Failed to save M365 email attachments",
                                account_id=account_id,
                                message_id=msg_id,
                                ticket_id=ticket_id,
                                error=str(exc),
                            )

                await _record_message(
                    account_id=int(account_id),
                    uid=msg_id,
                    status="imported",
                    ticket_id=int(ticket_id) if isinstance(ticket_id, int) else None,
                    error=None,
                )
                processed += 1

                # Mark as read if configured
                if mark_as_read and is_unread:
                    try:
                        patch_url = f"{_GRAPH_BASE}/users/{quote(upn, safe='')}/messages/{msg_id}"
                        await _graph_patch(access_token, patch_url, {"isRead": True})
                    except Exception:  # pragma: no cover - Graph API errors
                        log_error(
                            "Unable to mark M365 message as read",
                            account_id=account_id,
                            message_id=msg_id,
                        )

    except Exception as exc:  # pragma: no cover - network interaction
        log_error("M365 mail synchronisation failed", account_id=account_id, error=str(exc))
        errors.append({"error": str(exc)})

    await mail_repo.update_account(
        int(account_id),
        last_synced_at=datetime.now(timezone.utc),
    )
    log_info(
        "M365 mail synchronisation completed",
        account_id=account_id,
        processed=processed,
        errors=len(errors),
    )
    status_value = "succeeded" if not errors else "completed_with_errors"
    return {"status": status_value, "processed": processed, "errors": errors}


async def _save_graph_attachments(
    *,
    access_token: str,
    upn: str,
    message_id: str,
    ticket_id: int,
) -> None:
    """Fetch and save message attachments via the Graph API."""
    url = f"{_GRAPH_BASE}/users/{quote(upn, safe='')}/messages/{message_id}/attachments"
    try:
        data = await _graph_get(access_token, url)
    except Exception:
        return

    for attachment in data.get("value") or []:
        odata_type = attachment.get("@odata.type") or ""
        if odata_type != "#microsoft.graph.fileAttachment":
            continue

        attachment_id = str(attachment.get("id") or "").strip()
        filename = attachment.get("name") or "attachment"
        content_type = attachment.get("contentType") or "application/octet-stream"
        content_bytes_b64 = attachment.get("contentBytes") or ""
        payload: bytes | None = None

        if content_bytes_b64:
            try:
                payload = base64.b64decode(content_bytes_b64)
            except Exception:
                payload = None

        if payload is None and attachment_id:
            # Graph list-attachments responses may omit contentBytes depending on
            # tenant settings, API behavior, or attachment size. Fetch the raw
            # attachment stream to ensure file attachments are persisted.
            value_url = (
                f"{_GRAPH_BASE}/users/{quote(upn, safe='')}/messages/{message_id}"
                f"/attachments/{quote(attachment_id, safe='')}/$value"
            )
            try:
                payload = await _graph_get_bytes(access_token, value_url)
            except Exception:
                payload = None

        if not payload:
            continue

        # Skip inline images (same as IMAP)
        is_inline = attachment.get("isInline", False)
        main_type = content_type.split("/")[0].lower() if "/" in content_type else ""
        if is_inline and main_type == "image":
            continue

        # Reuse the shared attachment saver from the IMAP module
        try:
            await _save_email_attachment(
                ticket_id=ticket_id,
                filename=filename,
                content_type=content_type,
                payload=payload,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to save M365 email attachment",
                ticket_id=ticket_id,
                filename=filename,
                error=str(exc),
            )


async def sync_all_active() -> None:
    """Synchronise all active Office 365 mail accounts in priority order."""
    accounts = await mail_repo.list_accounts()
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
                "Failed to synchronise M365 mail account during bulk run",
                account_id=account.get("id"),
                error=str(exc),
            )
