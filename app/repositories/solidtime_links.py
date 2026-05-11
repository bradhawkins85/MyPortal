"""CRUD helpers for Solidtime integration mapping tables.

The Solidtime module persists four small mapping tables introduced in
``migrations/245_solidtime_integration.sql``:

* ``solidtime_client_links`` – company → Solidtime client
* ``solidtime_project_links`` – ticket → Solidtime project
* ``solidtime_time_entry_links`` – ticket reply → Solidtime time entry
* ``solidtime_user_links`` – MyPortal user → Solidtime member

All helpers are async and use the shared ``app.core.database.db`` pool.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db

LinkRecord = dict[str, Any]


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _normalise(row: dict[str, Any] | None) -> LinkRecord | None:
    if not row:
        return None
    record = dict(row)
    for key in ("created_at", "updated_at", "last_synced_at"):
        if key in record:
            record[key] = _make_aware(record.get(key))
    return record


# ---------------------------------------------------------------------------
# Client links (companies → Solidtime clients)
# ---------------------------------------------------------------------------

async def get_client_link(company_id: int) -> LinkRecord | None:
    row = await db.fetch_one(
        "SELECT * FROM solidtime_client_links WHERE company_id = %s",
        (int(company_id),),
    )
    return _normalise(row)


async def upsert_client_link(
    *,
    company_id: int,
    solidtime_org_id: str,
    solidtime_client_id: str,
    sync_status: str = "synced",
    last_error: str | None = None,
) -> LinkRecord | None:
    await db.execute(
        """
        INSERT INTO solidtime_client_links
            (company_id, solidtime_org_id, solidtime_client_id, sync_status, last_error, last_synced_at)
        VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP(6))
        ON DUPLICATE KEY UPDATE
            solidtime_org_id = VALUES(solidtime_org_id),
            solidtime_client_id = VALUES(solidtime_client_id),
            sync_status = VALUES(sync_status),
            last_error = VALUES(last_error),
            last_synced_at = UTC_TIMESTAMP(6),
            updated_at = UTC_TIMESTAMP(6)
        """,
        (
            int(company_id),
            str(solidtime_org_id),
            str(solidtime_client_id),
            sync_status,
            last_error,
        ),
    )
    return await get_client_link(company_id)


async def mark_client_link_error(company_id: int, error: str) -> None:
    await db.execute(
        """
        INSERT INTO solidtime_client_links
            (company_id, solidtime_org_id, solidtime_client_id, sync_status, last_error)
        VALUES (%s, '', '', 'error', %s)
        ON DUPLICATE KEY UPDATE
            sync_status = 'error',
            last_error = VALUES(last_error),
            updated_at = UTC_TIMESTAMP(6)
        """,
        (int(company_id), error),
    )


# ---------------------------------------------------------------------------
# Project links (tickets → Solidtime projects)
# ---------------------------------------------------------------------------

async def get_project_link(ticket_id: int) -> LinkRecord | None:
    row = await db.fetch_one(
        "SELECT * FROM solidtime_project_links WHERE ticket_id = %s",
        (int(ticket_id),),
    )
    return _normalise(row)


async def get_project_link_by_remote(
    solidtime_org_id: str, solidtime_project_id: str
) -> LinkRecord | None:
    row = await db.fetch_one(
        """
        SELECT * FROM solidtime_project_links
        WHERE solidtime_org_id = %s AND solidtime_project_id = %s
        """,
        (str(solidtime_org_id), str(solidtime_project_id)),
    )
    return _normalise(row)


async def upsert_project_link(
    *,
    ticket_id: int,
    solidtime_org_id: str,
    solidtime_project_id: str,
    payload_hash: str | None = None,
    sync_status: str = "synced",
    last_error: str | None = None,
) -> LinkRecord | None:
    await db.execute(
        """
        INSERT INTO solidtime_project_links
            (ticket_id, solidtime_org_id, solidtime_project_id,
             last_payload_hash, sync_status, last_error, last_synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(6))
        ON DUPLICATE KEY UPDATE
            solidtime_org_id = VALUES(solidtime_org_id),
            solidtime_project_id = VALUES(solidtime_project_id),
            last_payload_hash = VALUES(last_payload_hash),
            sync_status = VALUES(sync_status),
            last_error = VALUES(last_error),
            last_synced_at = UTC_TIMESTAMP(6),
            updated_at = UTC_TIMESTAMP(6)
        """,
        (
            int(ticket_id),
            str(solidtime_org_id),
            str(solidtime_project_id),
            payload_hash,
            sync_status,
            last_error,
        ),
    )
    return await get_project_link(ticket_id)


async def mark_project_link_error(ticket_id: int, error: str) -> None:
    await db.execute(
        """
        INSERT INTO solidtime_project_links
            (ticket_id, solidtime_org_id, solidtime_project_id, sync_status, last_error)
        VALUES (%s, '', '', 'error', %s)
        ON DUPLICATE KEY UPDATE
            sync_status = 'error',
            last_error = VALUES(last_error),
            updated_at = UTC_TIMESTAMP(6)
        """,
        (int(ticket_id), error),
    )


async def list_project_links_with_errors(limit: int = 100) -> list[LinkRecord]:
    rows = await db.fetch_all(
        """
        SELECT * FROM solidtime_project_links
        WHERE sync_status = 'error'
        ORDER BY updated_at DESC
        LIMIT %s
        """,
        (int(limit),),
    )
    return [normalised for row in rows if (normalised := _normalise(row)) is not None]


# ---------------------------------------------------------------------------
# Time entry links
# ---------------------------------------------------------------------------

async def get_time_entry_link(reply_id: int) -> LinkRecord | None:
    row = await db.fetch_one(
        "SELECT * FROM solidtime_time_entry_links WHERE ticket_reply_id = %s",
        (int(reply_id),),
    )
    return _normalise(row)


async def get_time_entry_link_by_remote(
    solidtime_org_id: str, solidtime_time_entry_id: str
) -> LinkRecord | None:
    row = await db.fetch_one(
        """
        SELECT * FROM solidtime_time_entry_links
        WHERE solidtime_org_id = %s AND solidtime_time_entry_id = %s
        """,
        (str(solidtime_org_id), str(solidtime_time_entry_id)),
    )
    return _normalise(row)


async def upsert_time_entry_link(
    *,
    ticket_reply_id: int,
    solidtime_org_id: str,
    solidtime_time_entry_id: str,
    direction: str = "out",
    payload_hash: str | None = None,
    sync_status: str = "synced",
    last_error: str | None = None,
) -> LinkRecord | None:
    if direction not in {"out", "in"}:
        raise ValueError("direction must be 'out' or 'in'")
    await db.execute(
        """
        INSERT INTO solidtime_time_entry_links
            (ticket_reply_id, solidtime_org_id, solidtime_time_entry_id,
             direction, last_payload_hash, sync_status, last_error, last_synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(6))
        ON DUPLICATE KEY UPDATE
            solidtime_org_id = VALUES(solidtime_org_id),
            solidtime_time_entry_id = VALUES(solidtime_time_entry_id),
            direction = VALUES(direction),
            last_payload_hash = VALUES(last_payload_hash),
            sync_status = VALUES(sync_status),
            last_error = VALUES(last_error),
            last_synced_at = UTC_TIMESTAMP(6),
            updated_at = UTC_TIMESTAMP(6)
        """,
        (
            int(ticket_reply_id),
            str(solidtime_org_id),
            str(solidtime_time_entry_id),
            direction,
            payload_hash,
            sync_status,
            last_error,
        ),
    )
    return await get_time_entry_link(ticket_reply_id)


async def mark_time_entry_link_error(reply_id: int, error: str) -> None:
    await db.execute(
        """
        INSERT INTO solidtime_time_entry_links
            (ticket_reply_id, solidtime_org_id, solidtime_time_entry_id,
             direction, sync_status, last_error)
        VALUES (%s, '', '', 'out', 'error', %s)
        ON DUPLICATE KEY UPDATE
            sync_status = 'error',
            last_error = VALUES(last_error),
            updated_at = UTC_TIMESTAMP(6)
        """,
        (int(reply_id), error),
    )


async def delete_time_entry_link(reply_id: int) -> None:
    await db.execute(
        "DELETE FROM solidtime_time_entry_links WHERE ticket_reply_id = %s",
        (int(reply_id),),
    )


async def list_time_entry_links_for_ticket(ticket_id: int) -> list[LinkRecord]:
    rows = await db.fetch_all(
        """
        SELECT tel.*
        FROM solidtime_time_entry_links tel
        INNER JOIN ticket_replies tr ON tr.id = tel.ticket_reply_id
        WHERE tr.ticket_id = %s
        ORDER BY tel.updated_at DESC
        """,
        (int(ticket_id),),
    )
    return [normalised for row in rows if (normalised := _normalise(row)) is not None]


# ---------------------------------------------------------------------------
# User links
# ---------------------------------------------------------------------------

async def get_user_link(user_id: int) -> LinkRecord | None:
    row = await db.fetch_one(
        "SELECT * FROM solidtime_user_links WHERE user_id = %s",
        (int(user_id),),
    )
    return _normalise(row)


async def upsert_user_link(
    *,
    user_id: int,
    solidtime_org_id: str,
    solidtime_member_id: str,
) -> LinkRecord | None:
    await db.execute(
        """
        INSERT INTO solidtime_user_links
            (user_id, solidtime_org_id, solidtime_member_id)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            solidtime_org_id = VALUES(solidtime_org_id),
            solidtime_member_id = VALUES(solidtime_member_id),
            updated_at = UTC_TIMESTAMP(6)
        """,
        (int(user_id), str(solidtime_org_id), str(solidtime_member_id)),
    )
    return await get_user_link(user_id)


async def delete_user_link(user_id: int) -> None:
    await db.execute(
        "DELETE FROM solidtime_user_links WHERE user_id = %s",
        (int(user_id),),
    )
