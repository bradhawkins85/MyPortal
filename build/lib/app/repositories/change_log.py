from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db


def _normalise_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalise_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    normalised = dict(row)
    occurred_at = _normalise_datetime(normalised.get("occurred_at_utc"))
    if occurred_at is not None:
        normalised["occurred_at_utc"] = occurred_at
    guid = normalised.get("guid")
    if guid is not None:
        normalised["guid"] = str(guid)
    summary = normalised.get("summary")
    if isinstance(summary, bytes):
        normalised["summary"] = summary.decode("utf-8", errors="ignore")
    source_file = normalised.get("source_file")
    if source_file is not None:
        normalised["source_file"] = str(source_file)
    content_hash = normalised.get("content_hash")
    if content_hash is not None:
        normalised["content_hash"] = str(content_hash)
    return normalised


async def get_change_by_hash(content_hash: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT guid, occurred_at_utc, change_type, summary, source_file, content_hash FROM change_log WHERE content_hash = %s",
        (content_hash,),
    )
    return _normalise_row(row)


async def get_change_by_guid(guid: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT guid, occurred_at_utc, change_type, summary, source_file, content_hash FROM change_log WHERE guid = %s",
        (guid,),
    )
    return _normalise_row(row)


async def upsert_change(
    *,
    guid: str,
    occurred_at_utc: datetime,
    change_type: str,
    summary: str,
    source_file: str | None,
    content_hash: str,
) -> None:
    if occurred_at_utc.tzinfo is None:
        occurred_at_utc = occurred_at_utc.replace(tzinfo=timezone.utc)
    else:
        occurred_at_utc = occurred_at_utc.astimezone(timezone.utc)

    await db.execute(
        """
        INSERT INTO change_log (guid, occurred_at_utc, change_type, summary, source_file, content_hash)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            occurred_at_utc = VALUES(occurred_at_utc),
            change_type = VALUES(change_type),
            summary = VALUES(summary),
            source_file = VALUES(source_file),
            content_hash = VALUES(content_hash),
            updated_at_utc = CURRENT_TIMESTAMP(6)
        """,
        (
            guid,
            occurred_at_utc.replace(tzinfo=timezone.utc),
            change_type,
            summary,
            source_file,
            content_hash,
        ),
    )


async def list_change_log_entries(
    *, change_type: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if change_type:
        clauses.append("change_type = %s")
        params.append(change_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = await db.fetch_all(
        f"""
        SELECT guid, occurred_at_utc, change_type, summary, source_file, content_hash
        FROM change_log
        {where}
        ORDER BY occurred_at_utc DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return [row for row in (_normalise_row(row) for row in rows) if row]


async def list_change_types() -> list[str]:
    rows = await db.fetch_all(
        "SELECT DISTINCT change_type FROM change_log WHERE change_type IS NOT NULL ORDER BY change_type ASC"
    )
    types: list[str] = []
    for row in rows:
        value = row.get("change_type") if isinstance(row, dict) else None
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                types.append(stripped)
    return types
