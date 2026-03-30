from __future__ import annotations
import json

from datetime import datetime, timezone
from typing import Any

from app.core.database import db


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _normalise_account(row: dict[str, Any]) -> dict[str, Any]:
    account = dict(row)
    for key in ("id", "company_id", "scheduled_task_id", "priority"):
        if key in account and account[key] is not None:
            account[key] = int(account[key])
    for key in ("process_unread_only", "mark_as_read", "sync_known_only", "active"):
        if key in account:
            account[key] = bool(int(account[key]))
    for key in ("last_synced_at", "token_expires_at", "created_at", "updated_at"):
        if key in account:
            account[key] = _make_aware(account.get(key))
    raw_filter = account.get("filter_query")
    if isinstance(raw_filter, (bytes, bytearray)):
        raw_filter = raw_filter.decode("utf-8", errors="ignore")
    if isinstance(raw_filter, str):
        trimmed = raw_filter.strip()
        if not trimmed:
            account["filter_query"] = None
        else:
            try:
                account["filter_query"] = json.loads(trimmed)
            except json.JSONDecodeError:
                account["filter_query"] = None
    elif raw_filter is None:
        account["filter_query"] = None
    return account


async def list_accounts() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM m365_mail_accounts ORDER BY priority ASC, name ASC, id ASC"
    )
    return [_normalise_account(row) for row in rows]


async def get_account(account_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM m365_mail_accounts WHERE id = %s",
        (account_id,),
    )
    return _normalise_account(row) if row else None


async def create_account(
    *,
    name: str,
    company_id: int | None,
    user_principal_name: str,
    mailbox_type: str,
    folder: str,
    schedule_cron: str,
    filter_query: str | None,
    process_unread_only: bool,
    mark_as_read: bool,
    sync_known_only: bool,
    active: bool,
    scheduled_task_id: int | None = None,
    priority: int = 100,
) -> dict[str, Any]:
    account_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO m365_mail_accounts (
            company_id,
            name,
            user_principal_name,
            mailbox_type,
            folder,
            process_unread_only,
            mark_as_read,
            sync_known_only,
            schedule_cron,
            filter_query,
            active,
            scheduled_task_id,
            priority
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            company_id,
            name,
            user_principal_name,
            mailbox_type,
            folder,
            1 if process_unread_only else 0,
            1 if mark_as_read else 0,
            1 if sync_known_only else 0,
            schedule_cron,
            filter_query,
            1 if active else 0,
            scheduled_task_id,
            priority,
        ),
    )
    created = await get_account(int(account_id)) if account_id else None
    return created or {}


async def update_account(account_id: int, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return await get_account(account_id)
    assignments: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key not in {
            "name",
            "company_id",
            "tenant_id",
            "user_principal_name",
            "mailbox_type",
            "folder",
            "schedule_cron",
            "filter_query",
            "process_unread_only",
            "mark_as_read",
            "sync_known_only",
            "active",
            "scheduled_task_id",
            "last_synced_at",
            "priority",
            "refresh_token",
            "access_token",
            "token_expires_at",
        }:
            continue
        if key in {"process_unread_only", "mark_as_read", "sync_known_only", "active"}:
            assignments.append(f"{key} = %s")
            params.append(1 if value else 0)
        elif key in ("last_synced_at", "token_expires_at"):
            assignments.append(f"{key} = %s")
            if isinstance(value, datetime):
                params.append(value.replace(tzinfo=None))
            else:
                params.append(value)
        elif key == "priority":
            assignments.append("priority = %s")
            params.append(int(value))
        else:
            assignments.append(f"{key} = %s")
            params.append(value)
    if not assignments:
        return await get_account(account_id)
    assignments.append("updated_at = UTC_TIMESTAMP(6)")
    params.append(account_id)
    await db.execute(
        f"UPDATE m365_mail_accounts SET {', '.join(assignments)} WHERE id = %s",
        tuple(params),
    )
    return await get_account(account_id)


async def update_account_tokens(
    account_id: int,
    *,
    tenant_id: str | None,
    refresh_token: str | None,
    access_token: str | None,
    token_expires_at: datetime | None,
) -> dict[str, Any] | None:
    """Update only the OAuth token columns for a mail account."""
    expires_value = None
    if isinstance(token_expires_at, datetime):
        expires_value = token_expires_at.replace(tzinfo=None)
    await db.execute(
        """
        UPDATE m365_mail_accounts
        SET tenant_id = %s,
            refresh_token = %s,
            access_token = %s,
            token_expires_at = %s,
            updated_at = UTC_TIMESTAMP(6)
        WHERE id = %s
        """,
        (tenant_id, refresh_token, access_token, expires_value, account_id),
    )
    return await get_account(account_id)


async def clear_account_tokens(account_id: int) -> dict[str, Any] | None:
    """Remove the per-account OAuth tokens (disconnect)."""
    await db.execute(
        """
        UPDATE m365_mail_accounts
        SET tenant_id = NULL,
            refresh_token = NULL,
            access_token = NULL,
            token_expires_at = NULL,
            updated_at = UTC_TIMESTAMP(6)
        WHERE id = %s
        """,
        (account_id,),
    )
    return await get_account(account_id)


async def delete_account(account_id: int) -> None:
    await db.execute("DELETE FROM m365_mail_accounts WHERE id = %s", (account_id,))


def _normalise_message(row: dict[str, Any]) -> dict[str, Any]:
    message = dict(row)
    for key in ("id", "account_id", "ticket_id"):
        if key in message and message[key] is not None:
            message[key] = int(message[key])
    for key in ("processed_at", "created_at"):
        if key in message:
            message[key] = _make_aware(message.get(key))
    return message


async def get_message(account_id: int, message_uid: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT *
        FROM m365_mail_account_messages
        WHERE account_id = %s AND message_uid = %s
        """,
        (account_id, message_uid),
    )
    return _normalise_message(row) if row else None


async def upsert_message(
    *,
    account_id: int,
    message_uid: str,
    status: str,
    ticket_id: int | None,
    error: str | None,
    processed_at: datetime | None,
) -> None:
    await db.execute(
        """
        INSERT INTO m365_mail_account_messages (
            account_id,
            message_uid,
            status,
            ticket_id,
            error,
            processed_at
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            status = VALUES(status),
            ticket_id = VALUES(ticket_id),
            error = VALUES(error),
            processed_at = VALUES(processed_at)
        """,
        (
            account_id,
            message_uid,
            status,
            ticket_id,
            error,
            processed_at.replace(tzinfo=None) if isinstance(processed_at, datetime) else processed_at,
        ),
    )
