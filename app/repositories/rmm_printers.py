"""Storage for installed printers per RMM device (:mod:`app.services.rmm_printers`)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

# Insert order of the values each printer stores.
PRINTER_COLUMNS = (
    "name", "driver_name", "port_name", "port_address", "printer_type", "device_type", "status", "printer_status",
    "job_count", "is_shared",
    "share_name", "is_published", "location", "comment", "computer_name", "connected_users", "default_for",
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
    return item


async def get_scan(agent_id: int) -> dict[str, Any] | None:
    return _scan_row(await db.fetch_one("SELECT * FROM rmm_printer_scans WHERE agent_id = %s", (agent_id,)))


async def list_printers(agent_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM rmm_printers WHERE agent_id = %s ORDER BY is_installed DESC, name", (agent_id,),
    )
    return [dict(row) for row in rows or []]


async def _ensure_scan(agent: dict[str, Any]) -> int:
    existing = await db.fetch_one("SELECT id FROM rmm_printer_scans WHERE agent_id = %s", (int(agent["id"]),))
    if existing:
        return int(existing["id"])
    now = _utcnow()
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_printer_scans (agent_id, company_id, asset_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (int(agent["id"]), agent.get("company_id"), agent.get("asset_id"), now, now),
    )


async def mark_scan_requested(agent: dict[str, Any], user_id: int | None) -> None:
    row_id = await _ensure_scan(agent)
    now = _utcnow()
    await db.execute(
        "UPDATE rmm_printer_scans SET scan_requested_at = %s, scan_requested_by_user_id = %s, "
        "updated_at = %s WHERE id = %s",
        (now, user_id, now, row_id),
    )


async def save_scan(agent: dict[str, Any], *, run_id: int | None, scanned_at: datetime,
                    printers: list[dict[str, Any]] | None, errors: list[str],
                    forget_before: datetime | None = None) -> None:
    """Store a scan. Printers it reports are added or updated and marked
    installed; printers it no longer reports are kept, marked not installed,
    with their last_seen_at unchanged, until last seen before ``forget_before``.
    ``printers`` of ``None`` means the list could not be read, so nothing
    MyPortal already has changes."""

    row_id = await _ensure_scan(agent)
    agent_id = int(agent["id"])
    now = _utcnow()
    if printers is not None:
        known = await db.fetch_all("SELECT id, name FROM rmm_printers WHERE agent_id = %s", (agent_id,))
        ids = {str(row["name"]).casefold(): int(row["id"]) for row in known or []}
        updates, inserts = [], []
        for item in printers:
            values = tuple(item.get(column) for column in PRINTER_COLUMNS)
            existing = ids.pop(item["name"].casefold(), None)
            if existing is None:
                inserts.append((agent_id, *values, now, now))
            else:
                updates.append((*values, now, existing))
        if updates:
            await db.execute_many(
                "UPDATE rmm_printers SET name = %s, driver_name = %s, port_name = %s, port_address = %s, "
                "printer_type = %s, device_type = %s, status = %s, printer_status = %s, job_count = %s, "
                "is_shared = %s, share_name = %s, is_published = %s, location = %s, comment = %s, "
                "computer_name = %s, connected_users = %s, "
                "default_for = %s, is_installed = 1, last_seen_at = %s WHERE id = %s",
                updates,
            )
        if inserts:
            await db.execute_many(
                "INSERT INTO rmm_printers (agent_id, name, driver_name, port_name, port_address, "
                "printer_type, device_type, status, printer_status, job_count, is_shared, share_name, is_published, "
                "location, comment, computer_name, connected_users, default_for, first_seen_at, last_seen_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                inserts,
            )
        if ids:
            await db.execute_many(
                "UPDATE rmm_printers SET is_installed = 0 WHERE id = %s", [(printer_id,) for printer_id in ids.values()],
            )
    if forget_before is not None:
        await db.execute(
            "DELETE FROM rmm_printers WHERE agent_id = %s AND is_installed = 0 AND last_seen_at < %s",
            (agent_id, forget_before),
        )
    count = await db.fetch_one(
        "SELECT COUNT(*) AS total FROM rmm_printers WHERE agent_id = %s AND is_installed = 1", (agent_id,),
    )
    await db.execute(
        "UPDATE rmm_printer_scans SET company_id = %s, asset_id = %s, run_id = %s, scanned_at = %s, "
        "received_at = %s, printer_count = %s, errors_json = %s, updated_at = %s WHERE id = %s",
        (agent.get("company_id"), agent.get("asset_id"), run_id, scanned_at, now,
         int((count or {}).get("total") or 0), json.dumps(errors), now, row_id),
    )


async def has_active_scan(agent_id: int, run_source: str) -> bool:
    row = await db.fetch_one(
        "SELECT id FROM rmm_script_runs WHERE agent_id = %s AND run_source = %s "
        "AND status IN ('queued', 'dispatched', 'running') LIMIT 1",
        (agent_id, run_source),
    )
    return bool(row)


async def list_agents_due(*, run_source: str, received_before: datetime, requested_before: datetime,
                          seen_after: datetime, limit: int) -> list[dict[str, Any]]:
    """Connected Windows agents whose last printer scan is older than
    ``received_before``, not asked since ``requested_before``, with no scan waiting."""

    rows = await db.fetch_all(
        "SELECT g.* FROM rmm_agents g JOIN companies c ON c.id = g.company_id "
        "LEFT JOIN rmm_printer_scans p ON p.agent_id = g.id "
        "WHERE g.status = 'active' AND COALESCE(c.archived, 0) = 0 AND LOWER(g.os) = 'windows' "
        "AND g.last_seen_utc >= %s "
        "AND (p.received_at IS NULL OR p.received_at < %s) "
        "AND (p.scan_requested_at IS NULL OR p.scan_requested_at < %s) "
        "AND NOT EXISTS (SELECT 1 FROM rmm_script_runs r WHERE r.agent_id = g.id AND r.run_source = %s "
        "AND r.status IN ('queued', 'dispatched', 'running')) "
        "ORDER BY p.received_at IS NOT NULL, p.received_at, g.id LIMIT %s",
        (seen_after, received_before, requested_before, run_source, int(limit)),
    )
    return [dict(row) for row in rows or []]
