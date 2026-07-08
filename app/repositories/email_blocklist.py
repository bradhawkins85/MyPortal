from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from app.core.database import db

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ALLOWED_SORTS = {"email", "created_at", "updated_at", "source", "last_event_type"}


def normalize_email(address: str) -> str:
    normalised = str(address or "").strip().lower()
    if not normalised or len(normalised) > 320 or not _EMAIL_RE.match(normalised):
        raise ValueError("Enter a valid email address.")
    return normalised


async def list_entries(*, search: str | None = None, limit: int = 100, offset: int = 0, sort: str = "created_at", direction: str = "desc") -> list[dict[str, Any]]:
    sort_column = sort if sort in _ALLOWED_SORTS else "created_at"
    sort_direction = "ASC" if str(direction).lower() == "asc" else "DESC"
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 500)), "offset": max(0, int(offset))}
    where = ""
    if search:
        where = "WHERE email LIKE :search OR COALESCE(reason, '') LIKE :search OR COALESCE(last_event_type, '') LIKE :search"
        params["search"] = f"%{search.strip().lower()}%"
    return await db.fetch_all(
        f"""
        SELECT id, email, reason, source, last_event_type, last_event_payload,
               created_by_user_id, created_at, updated_at
        FROM email_blocklist
        {where}
        ORDER BY {sort_column} {sort_direction}, id {sort_direction}
        LIMIT :limit OFFSET :offset
        """,
        params,
    )


async def count_entries(*, search: str | None = None) -> int:
    params: dict[str, Any] = {}
    where = ""
    if search:
        where = "WHERE email LIKE :search OR COALESCE(reason, '') LIKE :search OR COALESCE(last_event_type, '') LIKE :search"
        params["search"] = f"%{search.strip().lower()}%"
    row = await db.fetch_one(f"SELECT COUNT(*) AS count FROM email_blocklist {where}", params)
    return int((row or {}).get("count") or 0)


async def get_by_email(address: str) -> dict[str, Any] | None:
    email = normalize_email(address)
    return await db.fetch_one("SELECT * FROM email_blocklist WHERE email = :email LIMIT 1", {"email": email})


async def is_blocked(address: str) -> bool:
    try:
        email = normalize_email(address)
    except ValueError:
        return False
    row = await db.fetch_one("SELECT id FROM email_blocklist WHERE email = :email LIMIT 1", {"email": email})
    return bool(row)


async def filter_allowed(addresses: list[str]) -> tuple[list[str], list[str]]:
    unique: list[str] = []
    seen: set[str] = set()
    for address in addresses:
        value = str(address or "").strip()
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            unique.append(value)
    if not unique:
        return [], []
    keys = [a.lower() for a in unique]
    placeholders = ", ".join(f":email{i}" for i in range(len(keys)))
    params = {f"email{i}": email for i, email in enumerate(keys)}
    rows = await db.fetch_all(f"SELECT email FROM email_blocklist WHERE email IN ({placeholders})", params)
    blocked = {str(row.get("email") or "").lower() for row in rows}
    return [a for a in unique if a.lower() not in blocked], [a for a in unique if a.lower() in blocked]


async def upsert_entry(*, email: str, reason: str | None = None, source: Literal["manual", "smtp2go_webhook"] = "manual", created_by_user_id: int | None = None, last_event_type: str | None = None, last_event_payload: str | None = None) -> dict[str, Any]:
    normalised = normalize_email(email)
    now = datetime.now(timezone.utc)
    existing = await get_by_email(normalised)
    payload = last_event_payload[:65535] if isinstance(last_event_payload, str) else None
    if existing:
        await db.execute(
            """
            UPDATE email_blocklist
            SET reason = COALESCE(:reason, reason),
                source = CASE WHEN source = 'manual' THEN source ELSE :source END,
                last_event_type = COALESCE(:last_event_type, last_event_type),
                last_event_payload = COALESCE(:last_event_payload, last_event_payload),
                updated_at = :updated_at
            WHERE email = :email
            """,
            {"email": normalised, "reason": reason, "source": source, "last_event_type": last_event_type, "last_event_payload": payload, "updated_at": now},
        )
    else:
        await db.execute(
            """
            INSERT INTO email_blocklist (email, reason, source, last_event_type, last_event_payload, created_by_user_id, created_at, updated_at)
            VALUES (:email, :reason, :source, :last_event_type, :last_event_payload, :created_by_user_id, :created_at, :updated_at)
            """,
            {"email": normalised, "reason": reason, "source": source, "last_event_type": last_event_type, "last_event_payload": payload, "created_by_user_id": created_by_user_id, "created_at": now, "updated_at": now},
        )
    row = await get_by_email(normalised)
    if row is None:
        raise RuntimeError("Failed to persist email blocklist entry")
    return row


async def delete_entry(entry_id: int) -> bool:
    return (await db.execute_rowcount("DELETE FROM email_blocklist WHERE id = :id", {"id": int(entry_id)})) > 0
