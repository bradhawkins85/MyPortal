"""Storage for Windows Update status per RMM device (:mod:`app.services.rmm_windows_updates`)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

# Insert order of the values each scan stores.
_MISSING_COLUMNS = (
    "update_id", "revision", "title", "kb", "classification", "severity", "size_bytes", "released_on",
    "is_downloaded", "reboot_behavior", "is_interactive", "is_optional", "is_hidden",
)
_HISTORY_COLUMNS = (
    "source", "update_id", "title", "kb", "classification", "operation", "result", "hresult", "occurred_at",
    "client_application",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _scan_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    raw = item.pop("errors_json", None)
    try:
        item["errors"] = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        item["errors"] = []
    item["reboot_pending"] = bool(item.get("reboot_pending"))
    return item


async def get_scan(agent_id: int) -> dict[str, Any] | None:
    return _scan_row(await db.fetch_one("SELECT * FROM rmm_windows_update_scans WHERE agent_id = %s", (agent_id,)))


async def list_missing(agent_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM rmm_windows_updates WHERE agent_id = %s ORDER BY released_on DESC, title", (agent_id,),
    )
    return [dict(row) for row in rows or []]


async def list_history(agent_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM rmm_windows_update_history WHERE agent_id = %s ORDER BY occurred_at DESC, id", (agent_id,),
    )
    return [dict(row) for row in rows or []]


async def _ensure_scan(agent: dict[str, Any]) -> int:
    existing = await db.fetch_one("SELECT id FROM rmm_windows_update_scans WHERE agent_id = %s", (int(agent["id"]),))
    if existing:
        return int(existing["id"])
    now = _utcnow()
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_windows_update_scans (agent_id, company_id, asset_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (int(agent["id"]), agent.get("company_id"), agent.get("asset_id"), now, now),
    )


async def mark_scan_requested(agent: dict[str, Any], user_id: int | None) -> None:
    row_id = await _ensure_scan(agent)
    now = _utcnow()
    await db.execute(
        "UPDATE rmm_windows_update_scans SET scan_requested_at = %s, scan_requested_by_user_id = %s, "
        "updated_at = %s WHERE id = %s",
        (now, user_id, now, row_id),
    )


async def save_scan(agent: dict[str, Any], *, run_id: int | None, scanned_at: datetime, source: str | None,
                    reboot_pending: bool, errors: list[str], missing: list[dict[str, Any]] | None,
                    history: list[dict[str, Any]] | None, hotfixes: list[dict[str, Any]] | None) -> None:
    """Store a scan. ``None`` for ``missing``, ``history`` or ``hotfixes`` means
    that part could not be read, so what MyPortal already has is kept."""

    row_id = await _ensure_scan(agent)
    agent_id = int(agent["id"])
    now = _utcnow()
    if missing is not None:
        known = await db.fetch_all(
            "SELECT update_id, first_seen_at FROM rmm_windows_updates WHERE agent_id = %s", (agent_id,),
        )
        first_seen = {str(row["update_id"]).lower(): row["first_seen_at"] for row in known or []}
        await db.execute("DELETE FROM rmm_windows_updates WHERE agent_id = %s", (agent_id,))
        if missing:
            await db.execute_many(
                "INSERT INTO rmm_windows_updates (agent_id, update_id, revision, title, kb, classification, severity, "
                "size_bytes, released_on, is_downloaded, reboot_behavior, is_interactive, is_optional, is_hidden, "
                "first_seen_at, last_seen_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(agent_id, *(item.get(column) for column in _MISSING_COLUMNS),
                  first_seen.get(item["update_id"].lower(), now), now) for item in missing],
            )
    for kind, rows in (("history", history), ("hotfix", hotfixes)):
        if rows is None:
            continue
        await db.execute("DELETE FROM rmm_windows_update_history WHERE agent_id = %s AND source = %s", (agent_id, kind))
        if rows:
            await db.execute_many(
                "INSERT INTO rmm_windows_update_history (agent_id, source, update_id, title, kb, classification, "
                "operation, result, hresult, occurred_at, client_application) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(agent_id, *({**item, "source": kind}.get(column) for column in _HISTORY_COLUMNS)) for item in rows],
            )
    count = await db.fetch_one("SELECT COUNT(*) AS total FROM rmm_windows_updates WHERE agent_id = %s", (agent_id,))
    await db.execute(
        "UPDATE rmm_windows_update_scans SET company_id = %s, asset_id = %s, run_id = %s, scanned_at = %s, "
        "received_at = %s, missing_checked_at = COALESCE(%s, missing_checked_at), update_source = %s, "
        "reboot_pending = %s, missing_count = %s, errors_json = %s, updated_at = %s WHERE id = %s",
        (agent.get("company_id"), agent.get("asset_id"), run_id, scanned_at, now,
         scanned_at if missing is not None else None, source, 1 if reboot_pending else 0,
         int((count or {}).get("total") or 0), json.dumps(errors), now, row_id),
    )


async def list_hidden(agent_id: int) -> list[str]:
    """The updates MyPortal hid on the device."""

    rows = await db.fetch_all(
        "SELECT update_id FROM rmm_windows_update_hidden WHERE agent_id = %s ORDER BY id", (agent_id,),
    )
    return [str(row["update_id"]) for row in rows or []]


async def save_hidden(agent_id: int, *, hid: list[str], unhid: list[str], present: set[str] | None) -> None:
    """Record what a scan hid and showed again. ``present`` is every update the
    scan found still needed; updates no longer offered stop being tracked."""

    now = _utcnow()
    known = set(await list_hidden(agent_id))
    new = [update_id for update_id in dict.fromkeys(hid) if update_id not in known]
    if new:
        await db.execute_many(
            "INSERT INTO rmm_windows_update_hidden (agent_id, update_id, hidden_at) VALUES (%s, %s, %s)",
            [(agent_id, update_id, now) for update_id in new],
        )
    gone = set(unhid) | ({update_id for update_id in known if update_id not in present} if present is not None else set())
    gone -= set(hid)
    if gone:
        await db.execute_many(
            "DELETE FROM rmm_windows_update_hidden WHERE agent_id = %s AND update_id = %s",
            [(agent_id, update_id) for update_id in sorted(gone)],
        )


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #

_BLOCK_COLUMNS = (
    "b.id, b.company_id, b.asset_id, b.kb, b.update_id, b.title, b.reason, b.created_by_user_id, b.created_at, "
    "a.name AS asset_name, c.name AS company_name, u.email AS created_by_email"
)
_BLOCK_JOINS = (
    "LEFT JOIN assets a ON a.id = b.asset_id LEFT JOIN companies c ON c.id = b.company_id "
    "LEFT JOIN users u ON u.id = b.created_by_user_id"
)


async def list_device_blocks(company_id: int, asset_id: int | None) -> list[dict[str, Any]]:
    """Blocks that reach one device: every company's, its company's and its own."""

    rows = await db.fetch_all(
        "SELECT " + _BLOCK_COLUMNS + " FROM rmm_windows_update_blocks b " + _BLOCK_JOINS + " "  # nosec B608 - fixed fragments
        "WHERE (b.company_id IS NULL AND b.asset_id IS NULL) "
        "OR (b.company_id = %s AND (b.asset_id IS NULL OR b.asset_id = %s)) ORDER BY b.id",
        (company_id, asset_id if asset_id is not None else -1),
    )
    return [dict(row) for row in rows or []]


async def list_company_blocks(company_id: int) -> list[dict[str, Any]]:
    """Every block a company's technicians see: global, the company's and its devices'."""

    rows = await db.fetch_all(
        "SELECT " + _BLOCK_COLUMNS + " FROM rmm_windows_update_blocks b " + _BLOCK_JOINS + " "  # nosec B608 - fixed fragments
        "WHERE (b.company_id IS NULL AND b.asset_id IS NULL) OR b.company_id = %s "
        "ORDER BY b.created_at DESC, b.id DESC",
        (company_id,),
    )
    return [dict(row) for row in rows or []]


async def get_block(block_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT " + _BLOCK_COLUMNS + " FROM rmm_windows_update_blocks b " + _BLOCK_JOINS + " "  # nosec B608 - fixed fragments
        "WHERE b.id = %s",
        (block_id,),
    )
    return dict(row) if row else None


async def find_block(*, company_id: int | None, asset_id: int | None, kb: str | None,
                     update_id: str | None) -> dict[str, Any] | None:
    """The same block at the same scope, if there is one."""

    rows = await db.fetch_all(
        "SELECT id, company_id, asset_id, kb, update_id FROM rmm_windows_update_blocks "
        "WHERE (kb = %s OR update_id = %s)",
        (kb or "", update_id or ""),
    )
    for row in rows or []:
        if row.get("company_id") == company_id and row.get("asset_id") == asset_id \
                and (row.get("kb") or None) == kb and (row.get("update_id") or None) == update_id:
            return dict(row)
    return None


async def create_block(*, company_id: int | None, asset_id: int | None, kb: str | None, update_id: str | None,
                       title: str, reason: str | None, user_id: int | None) -> int:
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_windows_update_blocks (company_id, asset_id, kb, update_id, title, reason, "
        "created_by_user_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (company_id, asset_id, kb, update_id, title, reason, user_id, _utcnow()),
    )


async def delete_block(block_id: int) -> None:
    await db.execute("DELETE FROM rmm_windows_update_blocks WHERE id = %s", (block_id,))


async def has_active_scan(agent_id: int, run_source: str) -> bool:
    row = await db.fetch_one(
        "SELECT id FROM rmm_script_runs WHERE agent_id = %s AND run_source = %s "
        "AND status IN ('queued', 'dispatched', 'running') LIMIT 1",
        (agent_id, run_source),
    )
    return bool(row)


async def list_agents_due(*, run_source: str, received_before: datetime, requested_before: datetime,
                          seen_after: datetime, limit: int) -> list[dict[str, Any]]:
    """Connected Windows agents whose last scan is older than ``received_before``,
    that were not asked since ``requested_before`` and have no scan waiting."""

    rows = await db.fetch_all(
        "SELECT g.* FROM rmm_agents g JOIN companies c ON c.id = g.company_id "
        "LEFT JOIN rmm_windows_update_scans w ON w.agent_id = g.id "
        "WHERE g.status = 'active' AND COALESCE(c.archived, 0) = 0 AND LOWER(g.os) = 'windows' "
        "AND g.last_seen_utc >= %s "
        "AND (w.received_at IS NULL OR w.received_at < %s) "
        "AND (w.scan_requested_at IS NULL OR w.scan_requested_at < %s) "
        "AND NOT EXISTS (SELECT 1 FROM rmm_script_runs r WHERE r.agent_id = g.id AND r.run_source = %s "
        "AND r.status IN ('queued', 'dispatched', 'running')) "
        "ORDER BY w.received_at IS NOT NULL, w.received_at, g.id LIMIT %s",
        (seen_after, received_before, requested_before, run_source, int(limit)),
    )
    return [dict(row) for row in rows or []]
