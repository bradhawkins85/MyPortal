"""Storage for the logs the AI agents asked for but could not read.

See :mod:`app.services.ai_required_logs`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db

_COLUMNS = (
    "id, source_key, platform, kind, source, reason_code, reason, detail, features, approve_kind, approve_value, "
    "occurrences, status, status_note, status_changed_by_user_id, status_changed_at, last_company_id, "
    "last_asset_id, last_ticket_id, last_health_check_id, first_seen_at, last_seen_at"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clip(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


async def get(entry_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT " + _COLUMNS + " FROM ai_required_logs WHERE id = %s",  # nosec B608 - fixed column list
        (int(entry_id),),
    )
    return dict(row) if row else None


async def get_by_key(source_key: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT " + _COLUMNS + " FROM ai_required_logs WHERE source_key = %s",  # nosec B608 - fixed column list
        (source_key,),
    )
    return dict(row) if row else None


async def insert(
    *,
    source_key: str,
    platform: str,
    kind: str,
    source: str,
    reason_code: str,
    reason: str | None,
    detail: str | None,
    features: str,
    approve_kind: str | None,
    approve_value: str | None,
    company_id: int | None,
    asset_id: int | None,
    ticket_id: int | None,
    health_check_id: int | None,
) -> int:
    now = _utcnow()
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO ai_required_logs (source_key, platform, kind, source, reason_code, reason, detail, features, "
        "approve_kind, approve_value, occurrences, status, last_company_id, last_asset_id, last_ticket_id, "
        "last_health_check_id, first_seen_at, last_seen_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 'open', %s, %s, %s, %s, %s, %s)",
        (source_key, platform, kind, source[:512], reason_code, _clip(reason, 2000), _clip(detail, 1024),
         features[:128], approve_kind, _clip(approve_value, 512), company_id, asset_id, ticket_id,
         health_check_id, now, now),
    ))


async def record_seen(
    entry_id: int,
    *,
    reason: str | None,
    detail: str | None,
    features: str,
    status: str,
    company_id: int | None,
    asset_id: int | None,
    ticket_id: int | None,
    health_check_id: int | None,
) -> None:
    """Count another request for a known log, keeping the latest context."""

    await db.execute(
        "UPDATE ai_required_logs SET occurrences = occurrences + 1, last_seen_at = %s, "
        "reason = COALESCE(%s, reason), detail = COALESCE(%s, detail), features = %s, status = %s, "
        "last_company_id = COALESCE(%s, last_company_id), last_asset_id = COALESCE(%s, last_asset_id), "
        "last_ticket_id = COALESCE(%s, last_ticket_id), "
        "last_health_check_id = COALESCE(%s, last_health_check_id) WHERE id = %s",
        (_utcnow(), _clip(reason, 2000), _clip(detail, 1024), features[:128], status, company_id, asset_id,
         ticket_id, health_check_id, int(entry_id)),
    )


async def list_entries(*, status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    if status:
        rows = await db.fetch_all(
            "SELECT " + _COLUMNS + " FROM ai_required_logs WHERE status = %s "  # nosec B608 - fixed column list
            "ORDER BY last_seen_at DESC, id DESC LIMIT %s",
            (status, int(limit)),
        )
    else:
        rows = await db.fetch_all(
            "SELECT " + _COLUMNS + " FROM ai_required_logs "  # nosec B608 - fixed column list
            "ORDER BY last_seen_at DESC, id DESC LIMIT %s",
            (int(limit),),
        )
    return [dict(row) for row in rows or []]


async def list_approved() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT platform, approve_kind, approve_value FROM ai_required_logs "
        "WHERE status = 'approved' AND approve_kind IS NOT NULL AND approve_value IS NOT NULL",
    )
    return [dict(row) for row in rows or []]


async def set_status(
    entry_id: int,
    *,
    status: str,
    note: str | None,
    user_id: int | None,
    approve_value: str | None = None,
) -> None:
    if approve_value is not None:
        await db.execute(
            "UPDATE ai_required_logs SET status = %s, status_note = %s, status_changed_by_user_id = %s, "
            "status_changed_at = %s, approve_value = %s WHERE id = %s",
            (status, _clip(note, 500), user_id, _utcnow(), approve_value[:512], int(entry_id)),
        )
        return
    await db.execute(
        "UPDATE ai_required_logs SET status = %s, status_note = %s, status_changed_by_user_id = %s, "
        "status_changed_at = %s WHERE id = %s",
        (status, _clip(note, 500), user_id, _utcnow(), int(entry_id)),
    )
