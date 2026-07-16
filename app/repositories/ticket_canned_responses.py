"""Repository helpers for ticket canned responses."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

CannedResponseRecord = dict[str, Any]


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _normalise_record(row: Mapping[str, Any] | None) -> CannedResponseRecord | None:
    if not row:
        return None
    record = dict(row)
    for key in ("id", "created_by_user_id"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    for key in ("created_at", "updated_at"):
        record[key] = _make_aware(record.get(key))
    return record


async def list_responses() -> list[CannedResponseRecord]:
    rows = await db.fetch_all(
        """
        SELECT id, title, body, created_by_user_id, created_at, updated_at
        FROM ticket_canned_responses
        ORDER BY title ASC, id ASC
        """
    )
    return [record for row in rows if (record := _normalise_record(row))]


async def get_response(response_id: int) -> CannedResponseRecord | None:
    row = await db.fetch_one(
        """
        SELECT id, title, body, created_by_user_id, created_at, updated_at
        FROM ticket_canned_responses
        WHERE id = %s
        """,
        (response_id,),
    )
    return _normalise_record(row)


async def create_response(*, title: str, body: str, created_by_user_id: int | None) -> CannedResponseRecord:
    response_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO ticket_canned_responses (title, body, created_by_user_id)
        VALUES (%s, %s, %s)
        """,
        (title, body, created_by_user_id),
    )
    row = await db.fetch_one(
        """
        SELECT id, title, body, created_by_user_id, created_at, updated_at
        FROM ticket_canned_responses
        WHERE id = %s
        """,
        (response_id,),
    )
    record = _normalise_record(row)
    if record is None:
        raise RuntimeError("Unable to load created canned response")
    return record


async def update_response(*, response_id: int, title: str, body: str) -> CannedResponseRecord | None:
    await db.execute(
        """
        UPDATE ticket_canned_responses
        SET title = %s, body = %s, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (title, body, response_id),
    )
    return await get_response(response_id)


async def delete_response(response_id: int) -> bool:
    existing = await get_response(response_id)
    if existing is None:
        return False
    await db.execute(
        """
        DELETE FROM ticket_canned_responses
        WHERE id = %s
        """,
        (response_id,),
    )
    return True
