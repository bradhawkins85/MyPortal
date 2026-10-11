"""Storage for RMM event logs.

The extra logs chosen per company and device are settings, kept in the main
database (``rmm_event_log_settings``). The entries agents send are kept in the
separate event log database (:mod:`app.core.event_log_database`).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.database import db
from app.core.event_log_database import ENTRIES_TABLE, event_log_db

_ENTRY_COLUMNS = (
    "company_id", "asset_id", "agent_id", "log_name", "record_id", "event_id", "level",
    "provider", "computer", "message", "time_created", "received_at",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _decode_logs(value: Any) -> list[str]:
    try:
        logs = json.loads(value) if value else []
    except (TypeError, ValueError):
        return []
    return [str(log) for log in logs if isinstance(log, str)] if isinstance(logs, list) else []


def _scope_key(company_id: int | None, asset_id: int | None) -> str:
    return f"asset:{int(asset_id)}" if asset_id is not None else f"company:{int(company_id)}"


async def get_extra_logs(*, company_id: int | None = None, asset_id: int | None = None) -> list[str]:
    row = await db.fetch_one(
        "SELECT extra_logs FROM rmm_event_log_settings WHERE scope_key = %s", (_scope_key(company_id, asset_id),)
    )
    return _decode_logs(row.get("extra_logs")) if row else []


async def list_asset_extra_logs(asset_ids: Iterable[int]) -> dict[int, list[str]]:
    keys = [f"asset:{int(asset_id)}" for asset_id in asset_ids]
    if not keys:
        return {}
    placeholders = ", ".join(["%s"] * len(keys))
    rows = await db.fetch_all(
        f"SELECT asset_id, extra_logs FROM rmm_event_log_settings WHERE scope_key IN ({placeholders})", tuple(keys)
    )
    return {int(row["asset_id"]): _decode_logs(row.get("extra_logs")) for row in rows}


async def save_extra_logs(
    logs: list[str], *, company_id: int | None = None, asset_id: int | None = None, user_id: int | None = None
) -> None:
    """Replace a company's or device's extra logs. An empty list removes the row."""

    key = _scope_key(company_id, asset_id)
    if not logs:
        await db.execute("DELETE FROM rmm_event_log_settings WHERE scope_key = %s", (key,))
        return
    payload = json.dumps(logs)
    existing = await db.fetch_one("SELECT scope_key FROM rmm_event_log_settings WHERE scope_key = %s", (key,))
    if existing:
        await db.execute(
            "UPDATE rmm_event_log_settings SET extra_logs = %s, updated_by_user_id = %s, updated_at = %s "
            "WHERE scope_key = %s",
            (payload, user_id, _utcnow(), key),
        )
        return
    await db.execute(
        "INSERT INTO rmm_event_log_settings (scope_key, company_id, asset_id, extra_logs, updated_by_user_id, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (key, None if asset_id is not None else company_id, asset_id, payload, user_id, _utcnow()),
    )


async def insert_entries(entries: list[dict[str, Any]]) -> int:
    """Store entries, skipping ones already stored. Returns how many were new."""

    placeholders = ", ".join(["%s"] * len(_ENTRY_COLUMNS))
    return await event_log_db.execute_many(
        f"INSERT IGNORE INTO {ENTRIES_TABLE} ({', '.join(_ENTRY_COLUMNS)}) VALUES ({placeholders})",
        [tuple(entry.get(column) for column in _ENTRY_COLUMNS) for entry in entries],
    )


async def list_entries(
    *,
    company_id: int,
    asset_id: int | None = None,
    log_name: str | None = None,
    max_level: int | None = None,
    event_id: int | None = None,
    search: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Newest entries first for a company, with optional filters.

    ``max_level`` keeps entries at that severity or worse (1 critical, 2 error,
    3 warning, 4 information); 0 is stored for "log always" entries and counts
    as information.
    """

    clauses = ["company_id = %s"]
    params: list[Any] = [company_id]
    if asset_id is not None:
        clauses.append("asset_id = %s")
        params.append(asset_id)
    if log_name:
        clauses.append("log_name = %s")
        params.append(log_name)
    if max_level is not None:
        clauses.append("level BETWEEN 1 AND %s")
        params.append(max_level)
    if event_id is not None:
        clauses.append("event_id = %s")
        params.append(event_id)
    if search:
        escaped = search.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        clauses.append("(message LIKE %s ESCAPE '!' OR provider LIKE %s ESCAPE '!')")
        params.extend([f"%{escaped}%", f"%{escaped}%"])
    params.append(int(limit))
    return await event_log_db.fetch_all(
        f"SELECT id, company_id, asset_id, agent_id, log_name, record_id, event_id, level, provider, computer, "
        f"message, time_created, received_at FROM {ENTRIES_TABLE} WHERE {' AND '.join(clauses)} "
        f"ORDER BY time_created DESC, id DESC LIMIT %s",
        tuple(params),
    )


async def prune_entries(before: datetime, *, batch_size: int = 10000) -> int:
    """Delete entries created before ``before``, in batches on MySQL."""

    if event_log_db.is_sqlite:
        return await event_log_db.execute_rowcount(
            f"DELETE FROM {ENTRIES_TABLE} WHERE time_created < %s", (before,)
        )
    removed = 0
    while True:
        count = await event_log_db.execute_rowcount(
            f"DELETE FROM {ENTRIES_TABLE} WHERE time_created < %s LIMIT {int(batch_size)}", (before,)
        )
        removed += count
        if count < batch_size:
            return removed
