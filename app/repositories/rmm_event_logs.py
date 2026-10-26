"""Storage for RMM event logs.

The extra logs and least severe level chosen per company and device are
settings, kept in the main database (``rmm_event_log_settings``). The entries
agents send are kept in the separate event log database
(:mod:`app.core.event_log_database`), with each distinct message text stored
once (:func:`message_ids`).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.core.database import db
from app.core.event_log_database import ENTRIES_TABLE, MESSAGES_TABLE, META_TABLE, event_log_db

_ENTRY_COLUMNS = (
    "company_id", "asset_id", "agent_id", "log_name", "record_id", "event_id", "level",
    "provider", "computer", "message_id", "repeat_count", "time_created", "last_time_created",
    "last_record_id", "received_at",
)
# Entries read with their message: from the messages table, or the entry's own
# text for entries stored before de-duplication.
_FROM_ENTRIES = f"{ENTRIES_TABLE} e LEFT JOIN {MESSAGES_TABLE} m ON m.id = e.message_id"
MESSAGE_SQL = "COALESCE(m.message, e.message)"
# A message's last_seen_at is refreshed at most this often, so a busy message
# is not rewritten on every batch.
_TOUCH_INTERVAL = timedelta(days=1)
_LEGACY_CURSOR = "legacy_messages_through_id"


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


def _decode_level(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _decode_settings(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"extra_logs": [], "max_level": None}
    return {"extra_logs": _decode_logs(row.get("extra_logs")), "max_level": _decode_level(row.get("max_level"))}


async def get_settings(*, company_id: int | None = None, asset_id: int | None = None) -> dict[str, Any]:
    """A company's or device's ``extra_logs`` and ``max_level`` (None when not chosen)."""

    row = await db.fetch_one(
        "SELECT extra_logs, max_level FROM rmm_event_log_settings WHERE scope_key = %s",
        (_scope_key(company_id, asset_id),),
    )
    return _decode_settings(row)


async def get_extra_logs(*, company_id: int | None = None, asset_id: int | None = None) -> list[str]:
    return (await get_settings(company_id=company_id, asset_id=asset_id))["extra_logs"]


async def list_asset_settings(asset_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    keys = [f"asset:{int(asset_id)}" for asset_id in asset_ids]
    if not keys:
        return {}
    placeholders = ", ".join(["%s"] * len(keys))
    # Only "%s" placeholders are interpolated; the keys are bound parameters.
    rows = await db.fetch_all(
        f"SELECT asset_id, extra_logs, max_level FROM rmm_event_log_settings WHERE scope_key IN ({placeholders})",  # nosec B608
        tuple(keys),
    )
    return {int(row["asset_id"]): _decode_settings(row) for row in rows}


async def list_asset_extra_logs(asset_ids: Iterable[int]) -> dict[int, list[str]]:
    return {asset_id: settings["extra_logs"] for asset_id, settings in (await list_asset_settings(asset_ids)).items()}


async def _save_setting(
    column: str, value: Any, *, company_id: int | None, asset_id: int | None, user_id: int | None
) -> None:
    key = _scope_key(company_id, asset_id)
    existing = await db.fetch_one("SELECT scope_key FROM rmm_event_log_settings WHERE scope_key = %s", (key,))
    # ``column`` is one of the two fixed names passed below.
    if existing:
        await db.execute(
            f"UPDATE rmm_event_log_settings SET {column} = %s, updated_by_user_id = %s, updated_at = %s "  # nosec B608
            "WHERE scope_key = %s",
            (value, user_id, _utcnow(), key),
        )
    else:
        values = {"extra_logs": "[]", column: value}
        await db.execute(
            "INSERT INTO rmm_event_log_settings (scope_key, company_id, asset_id, extra_logs, max_level, "
            "updated_by_user_id, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (key, None if asset_id is not None else company_id, asset_id, values["extra_logs"],
             values.get("max_level"), user_id, _utcnow()),
        )
    # A row with nothing chosen is removed.
    await db.execute(
        "DELETE FROM rmm_event_log_settings WHERE scope_key = %s AND extra_logs = '[]' AND max_level IS NULL", (key,)
    )


async def save_extra_logs(
    logs: list[str], *, company_id: int | None = None, asset_id: int | None = None, user_id: int | None = None
) -> None:
    """Replace a company's or device's extra logs."""

    await _save_setting("extra_logs", json.dumps(logs), company_id=company_id, asset_id=asset_id, user_id=user_id)


async def save_max_level(
    level: int | None, *, company_id: int | None = None, asset_id: int | None = None, user_id: int | None = None
) -> None:
    """Set a company's or device's least severe level kept; None uses the one above it."""

    await _save_setting("max_level", level, company_id=company_id, asset_id=asset_id, user_id=user_id)


def message_hash(message: str) -> bytes:
    return hashlib.sha256(message.encode("utf-8")).digest()


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


async def _find_messages(hashes: list[bytes]) -> dict[bytes, dict[str, Any]]:
    if not hashes:
        return {}
    placeholders = ", ".join(["%s"] * len(hashes))
    # Only "%s" placeholders are interpolated; the hashes are bound parameters.
    rows = await event_log_db.fetch_all(
        f"SELECT id, message_hash, last_seen_at FROM {MESSAGES_TABLE} WHERE message_hash IN ({placeholders})",  # nosec B608
        tuple(hashes),
    )
    return {bytes(row["message_hash"]): row for row in rows}


async def message_ids(messages: Iterable[str], *, seen_at: datetime) -> dict[str, int]:
    """The id of each distinct message text, storing the new ones."""

    by_hash = {message_hash(message): message for message in messages if message}
    if not by_hash:
        return {}
    found = await _find_messages(list(by_hash))
    missing = [digest for digest in by_hash if digest not in found]
    if missing:
        await event_log_db.execute_many(
            f"INSERT INTO {MESSAGES_TABLE} (message_hash, message, last_seen_at) VALUES (%s, %s, %s) "  # nosec B608
            "ON DUPLICATE KEY UPDATE id = id",
            [(digest, by_hash[digest], seen_at) for digest in missing],
        )
        found.update(await _find_messages(missing))
    stale_before = seen_at - _TOUCH_INTERVAL
    stale = [
        int(row["id"]) for digest, row in found.items()
        if digest not in missing and (_as_datetime(row.get("last_seen_at")) or seen_at) < stale_before
    ]
    if stale:
        # Only "%s" placeholders are interpolated; the ids are bound parameters.
        await event_log_db.execute_rowcount(
            f"UPDATE {MESSAGES_TABLE} SET last_seen_at = %s WHERE id IN ({', '.join(['%s'] * len(stale))})",  # nosec B608
            (seen_at, *stale),
        )
    return {by_hash[digest]: int(row["id"]) for digest, row in found.items()}


async def insert_entries(entries: list[dict[str, Any]]) -> int:
    """Store entries, skipping ones already stored. Returns how many were new.

    An entry gives its text as ``message_id`` (:func:`message_ids`) or as
    ``message``, which is stored in the messages table here.
    """

    texts = [str(entry["message"]) for entry in entries if entry.get("message") and entry.get("message_id") is None]
    if texts:
        ids = await message_ids(texts, seen_at=_utcnow())
        entries = [
            {**entry, "message_id": ids[str(entry["message"])]}
            if entry.get("message") and entry.get("message_id") is None else entry
            for entry in entries
        ]
    placeholders = ", ".join(["%s"] * len(_ENTRY_COLUMNS))
    return await event_log_db.execute_many(
        # A duplicate-key no-op is idempotent without making MySQL emit
        # warning 1062 through aiomysql on every ignored row.
        f"INSERT INTO {ENTRIES_TABLE} ({', '.join(_ENTRY_COLUMNS)}) VALUES ({placeholders}) "  # nosec B608 - fixed table name
        "ON DUPLICATE KEY UPDATE id = id",
        [tuple({"repeat_count": 1, **entry}.get(column) for column in _ENTRY_COLUMNS) for entry in entries],
    )


async def recent_repeats(*, agent_id: int, message_ids: Iterable[int], since: datetime) -> list[dict[str, Any]]:
    """A device's entries created since ``since`` with one of ``message_ids``
    or no message, which later occurrences of the same event can join."""

    ids = sorted({int(message_id) for message_id in message_ids})
    message_clause = f"e.message_id IN ({', '.join(['%s'] * len(ids))}) OR " if ids else ""
    # Only "%s" placeholders are interpolated; every value is a bound parameter.
    # Entries stored before de-duplication keep their own text and never join.
    return await event_log_db.fetch_all(
        "SELECT e.id, e.company_id, e.asset_id, e.log_name, e.record_id, e.event_id, e.level, e.provider, "
        "e.computer, e.message_id, e.repeat_count, e.time_created, e.last_time_created, e.last_record_id "
        f"FROM {ENTRIES_TABLE} e WHERE e.agent_id = %s AND ({message_clause}e.message_id IS NULL) "  # nosec B608
        "AND e.message IS NULL AND e.time_created >= %s ORDER BY e.time_created, e.id",
        (int(agent_id), *ids, since),
    )


async def add_repeats(updates: list[dict[str, Any]]) -> None:
    """Fold more occurrences into stored entries: each update has the entry's
    ``id``, the occurrences ``added`` and the new ``last_time_created``,
    ``last_record_id`` and ``received_at``."""

    await event_log_db.execute_many(
        f"UPDATE {ENTRIES_TABLE} SET repeat_count = repeat_count + %s, last_time_created = %s, "  # nosec B608
        "last_record_id = %s, received_at = %s WHERE id = %s",
        [
            (int(update["added"]), update["last_time_created"], int(update["last_record_id"]),
             update["received_at"], int(update["id"]))
            for update in updates
        ],
    )


def _like_escape(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


async def list_entries(
    *,
    company_id: int,
    asset_id: int | None = None,
    log_name: str | None = None,
    max_level: int | None = None,
    event_id: int | None = None,
    search: str | None = None,
    source: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Newest entries first for a company, with optional filters.

    ``source`` matches part of the source (provider); ``since`` and ``until``
    bound ``time_created`` (``until`` is exclusive).

    ``max_level`` keeps entries at that severity or worse (1 critical, 2 error,
    3 warning, 4 information); 0 is stored for "log always" entries and counts
    as information.

    An entry stands for ``repeat_count`` occurrences of the same event, from
    ``time_created`` to ``last_time_created`` (None for a single one).
    """

    clauses = ["e.company_id = %s"]
    params: list[Any] = [company_id]
    if asset_id is not None:
        clauses.append("e.asset_id = %s")
        params.append(asset_id)
    if log_name:
        clauses.append("e.log_name = %s")
        params.append(log_name)
    if max_level is not None:
        clauses.append("e.level BETWEEN 1 AND %s")
        params.append(max_level)
    if event_id is not None:
        clauses.append("e.event_id = %s")
        params.append(event_id)
    if search:
        escaped = _like_escape(search)
        # Each column on its own: a COALESCE of columns with different
        # collations cannot be compared on MySQL.
        clauses.append("(m.message LIKE %s ESCAPE '!' OR e.message LIKE %s ESCAPE '!' OR e.provider LIKE %s ESCAPE '!')")
        params.extend([f"%{escaped}%"] * 3)
    if source:
        clauses.append("e.provider LIKE %s ESCAPE '!'")
        params.append(f"%{_like_escape(source)}%")
    if since is not None:
        clauses.append("e.time_created >= %s")
        params.append(since)
    if until is not None:
        clauses.append("e.time_created < %s")
        params.append(until)
    params.append(int(limit))
    # The clauses are fixed strings chosen above; every value is a bound parameter.
    return await event_log_db.fetch_all(  # nosec B608
        "SELECT e.id, e.company_id, e.asset_id, e.agent_id, e.log_name, e.record_id, e.event_id, e.level, "
        f"e.provider, e.computer, {MESSAGE_SQL} AS message, e.repeat_count, e.time_created, "
        f"e.last_time_created, e.received_at FROM {_FROM_ENTRIES} WHERE {' AND '.join(clauses)} "  # nosec B608
        "ORDER BY e.time_created DESC, e.id DESC LIMIT %s",
        tuple(params),
    )


async def _delete_before(table: str, column: str, before: datetime, batch_size: int) -> int:
    # ``table`` and ``column`` are fixed names from the callers below.
    if event_log_db.is_sqlite:
        return await event_log_db.execute_rowcount(f"DELETE FROM {table} WHERE {column} < %s", (before,))  # nosec B608
    removed = 0
    while True:
        count = await event_log_db.execute_rowcount(
            f"DELETE FROM {table} WHERE {column} < %s LIMIT %s", (before, int(batch_size))  # nosec B608
        )
        removed += count
        if count < batch_size:
            return removed


async def prune_entries(before: datetime, *, batch_size: int = 10000) -> int:
    """Delete entries created before ``before``, in batches on MySQL."""

    return await _delete_before(ENTRIES_TABLE, "time_created", before, batch_size)


async def prune_messages(before: datetime, *, batch_size: int = 10000) -> int:
    """Delete message texts no entry has used since ``before``.

    An entry is stored or repeated when its message is last seen, and its
    message is seen again at least daily while it keeps arriving, so pass a
    ``before`` a day or more earlier than the entries' own cut-off.
    """

    return await _delete_before(MESSAGES_TABLE, "last_seen_at", before, batch_size)


async def _meta(name: str) -> int:
    rows = await event_log_db.fetch_all(f"SELECT value FROM {META_TABLE} WHERE name = %s", (name,))  # nosec B608
    return int(rows[0]["value"]) if rows else 0


async def _set_meta(name: str, value: int) -> None:
    # Only the event log prune writes these, one worker at a time.
    exists = await event_log_db.fetch_all(f"SELECT 1 AS found FROM {META_TABLE} WHERE name = %s", (name,))  # nosec B608
    if exists:
        await event_log_db.execute_rowcount(
            f"UPDATE {META_TABLE} SET value = %s WHERE name = %s", (int(value), name)  # nosec B608
        )
    else:
        await event_log_db.execute_rowcount(
            f"INSERT INTO {META_TABLE} (name, value) VALUES (%s, %s)", (name, int(value))  # nosec B608
        )


async def compact_legacy_messages(*, batch_size: int = 2000, max_rows: int = 1_000_000) -> int:
    """Move the text of entries stored before de-duplication into the messages
    table, at most ``max_rows`` a run. Returns how many entries moved.

    New entries never have their own text, so after a pass reaches the newest
    entry later passes only look at entries added since.
    """

    after = await _meta(_LEGACY_CURSOR)
    moved = 0
    while moved < max_rows:
        rows = await event_log_db.fetch_all(
            f"SELECT id, message FROM {ENTRIES_TABLE} WHERE id > %s ORDER BY id LIMIT %s",  # nosec B608
            (after, int(batch_size)),
        )
        if not rows:
            break
        legacy = [row for row in rows if row.get("message")]
        if legacy:
            ids = await message_ids((str(row["message"]) for row in legacy), seen_at=_utcnow())
            await event_log_db.execute_many(
                f"UPDATE {ENTRIES_TABLE} SET message_id = %s, message = NULL WHERE id = %s",  # nosec B608
                [(ids[str(row["message"])], int(row["id"])) for row in legacy if str(row["message"]) in ids],
            )
            moved += len(legacy)
        after = int(rows[-1]["id"])
        await _set_meta(_LEGACY_CURSOR, after)
        if len(rows) < batch_size:
            break
    return moved


async def scan_entries(
    *,
    company_id: int | None,
    asset_id: int | None,
    event_ids: list[int] | None,
    after_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Stored entries in id order after ``after_id``, for checking a rule
    against what is already stored. ``company_id`` None means every company."""

    clauses = ["e.id > %s"]
    params: list[Any] = [int(after_id)]
    if company_id is not None:
        clauses.append("e.company_id = %s")
        params.append(int(company_id))
    if asset_id is not None:
        clauses.append("e.asset_id = %s")
        params.append(int(asset_id))
    if event_ids:
        clauses.append("e.event_id IN (" + ", ".join(["%s"] * len(event_ids)) + ")")
        params.extend(int(event_id) for event_id in event_ids)
    params.append(int(limit))
    # The clauses are fixed strings chosen above; every value is a bound parameter.
    return await event_log_db.fetch_all(  # nosec B608
        "SELECT e.id, e.agent_id, e.log_name, e.event_id, e.level, e.provider, e.computer, "
        f"{MESSAGE_SQL} AS message, e.repeat_count FROM {_FROM_ENTRIES} "
        f"WHERE {' AND '.join(clauses)} ORDER BY e.id LIMIT %s",  # nosec B608
        tuple(params),
    )


async def delete_entries(ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ", ".join(["%s"] * len(ids))
    # Only "%s" placeholders are interpolated; the ids are bound parameters.
    return await event_log_db.execute_rowcount(
        f"DELETE FROM {ENTRIES_TABLE} WHERE id IN ({placeholders})",  # nosec B608
        tuple(int(entry_id) for entry_id in ids),
    )
