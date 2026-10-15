"""Storage for RMM device inventory (:mod:`app.services.rmm_inventory`)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.database import db

SUMMARY_COLUMNS = (
    "hostname", "manufacturer", "model", "serial_number", "bios_version", "os_name", "os_version", "os_build",
    "arch", "domain", "cpu_model", "cpu_cores", "cpu_logical_processors", "ram_bytes", "disk_total_bytes",
    "system_disk_free_bytes", "last_boot_at",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    raw = item.pop("details_json", None)
    try:
        item["details"] = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        item["details"] = {}
    return item


async def get_for_agent(agent_id: int) -> dict[str, Any] | None:
    return _row(await db.fetch_one("SELECT * FROM rmm_device_inventory WHERE agent_id = %s", (agent_id,)))


async def list_for_agents(agent_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    ids = sorted({int(agent_id) for agent_id in agent_ids})
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    # Only "%s" placeholders are interpolated; the ids are bound parameters.
    rows = await db.fetch_all(
        f"SELECT * FROM rmm_device_inventory WHERE agent_id IN ({placeholders})",  # nosec B608
        tuple(ids),
    )
    return {int(row["agent_id"]): _row(row) for row in rows or []}


async def list_software(agent_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT name, version, publisher, install_date, source FROM rmm_device_software "
        "WHERE agent_id = %s ORDER BY name, version",
        (agent_id,),
    )
    return [dict(row) for row in rows or []]


async def list_catalog_software_names() -> list[str]:
    """Current Software names on active Windows agents for Chocolatey lookups."""
    rows = await db.fetch_all(
        "SELECT DISTINCT s.name FROM rmm_device_software s "
        "INNER JOIN rmm_agents a ON a.id = s.agent_id "
        "WHERE a.status = 'active' AND LOWER(a.os) = 'windows' "
        "AND TRIM(s.name) <> '' ORDER BY s.name"
    )
    return [str(row['name']) for row in rows or []]


async def list_software_history(agent_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM rmm_device_software_history WHERE agent_id = %s "
        "ORDER BY first_seen_at DESC, id DESC", (agent_id,),
    )
    return [dict(row) for row in rows or []]


def _software_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (item["name"].casefold(), item.get("version") or "", item.get("source") or "")


async def _save_software_history(agent_id: int, software: list[dict[str, Any]], now: datetime) -> None:
    active = await db.fetch_all(
        "SELECT * FROM rmm_device_software_history WHERE agent_id = %s AND removed_at IS NULL", (agent_id,),
    )
    remaining = {_software_key(item): item for item in software}
    updates = []
    removals = []
    for row in active or []:
        item = remaining.get(_software_key(row))
        # A changed install date indicates a reinstall even when the version is unchanged.
        if item and (not item.get("install_date") or not row.get("install_date")
                     or str(item["install_date"]) == str(row["install_date"])):
            updates.append((now, item.get("publisher"), item.get("install_date"), row["id"]))
            remaining.pop(_software_key(row))
        else:
            removals.append((now, row["id"]))
    if updates:
        await db.execute_many(
            "UPDATE rmm_device_software_history SET last_seen_at = %s, publisher = %s, "
            "install_date = COALESCE(%s, install_date) WHERE id = %s", updates,
        )
    if removals:
        await db.execute_many("UPDATE rmm_device_software_history SET removed_at = %s WHERE id = %s", removals)
    if remaining:
        await db.execute_many(
            "INSERT INTO rmm_device_software_history "
            "(agent_id, name, version, publisher, install_date, source, first_seen_at, last_seen_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(agent_id, item["name"], item.get("version"), item.get("publisher"), item.get("install_date"),
              item.get("source"), now, now) for item in remaining.values()],
        )


async def list_software_for_agents(agent_ids: Iterable[int]) -> dict[int, list[dict[str, Any]]]:
    ids = sorted({int(agent_id) for agent_id in agent_ids})
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    # Only "%s" placeholders are interpolated; the ids are bound parameters.
    rows = await db.fetch_all(
        f"SELECT agent_id, name, version FROM rmm_device_software WHERE agent_id IN ({placeholders})",  # nosec B608
        tuple(ids),
    )
    software: dict[int, list[dict[str, Any]]] = {agent_id: [] for agent_id in ids}
    for row in rows or []:
        software[int(row["agent_id"])].append(dict(row))
    return software


async def _ensure_row(agent: dict[str, Any]) -> int:
    existing = await db.fetch_one("SELECT id FROM rmm_device_inventory WHERE agent_id = %s", (int(agent["id"]),))
    if existing:
        return int(existing["id"])
    now = _utcnow()
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_device_inventory (agent_id, company_id, asset_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (int(agent["id"]), agent.get("company_id"), agent.get("asset_id"), now, now),
    )


async def save_snapshot(agent: dict[str, Any], summary: dict[str, Any], details: dict[str, Any],
                        software: list[dict[str, Any]], *, collected_at: datetime) -> None:
    """Replace the current snapshot, retaining observed software installation intervals."""

    row_id = await _ensure_row(agent)
    now = _utcnow()
    agent_id = int(agent["id"])
    software_failed = any(str(error).lower().startswith(("software:", "powershell:", "system_profiler:"))
                          for error in details.get("errors", []))
    if software_failed:
        # Failed/truncated collection must not be interpreted as an uninstall.
        software = await list_software(agent_id)
    assignments = ", ".join(f"{column} = %s" for column in SUMMARY_COLUMNS)
    # The column names come from SUMMARY_COLUMNS, not from the agent.
    await db.execute(
        f"UPDATE rmm_device_inventory SET company_id = %s, asset_id = %s, collected_at = %s, received_at = %s, "  # nosec B608
        f"{assignments}, software_count = %s, details_json = %s, updated_at = %s WHERE id = %s",
        (
            agent.get("company_id"), agent.get("asset_id"), collected_at, now,
            *(summary.get(column) for column in SUMMARY_COLUMNS),
            len(software), json.dumps(details), now, row_id,
        ),
    )
    if software_failed:
        return
    await _save_software_history(agent_id, software, now)
    await db.execute("DELETE FROM rmm_device_software WHERE agent_id = %s", (agent_id,))
    if software:
        await db.execute_many(
            "INSERT INTO rmm_device_software (agent_id, name, version, publisher, install_date, source) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (agent_id, item["name"], item.get("version"), item.get("publisher"), item.get("install_date"),
                 item.get("source"))
                for item in software
            ],
        )


async def request_refresh(agent: dict[str, Any], user_id: int | None) -> None:
    row_id = await _ensure_row(agent)
    now = _utcnow()
    await db.execute(
        "UPDATE rmm_device_inventory SET refresh_requested_at = %s, refresh_requested_by_user_id = %s, "
        "updated_at = %s WHERE id = %s",
        (now, user_id, now, row_id),
    )
