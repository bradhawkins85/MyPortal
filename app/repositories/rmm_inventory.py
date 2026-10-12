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
    agent_id = int(agent["id"])
    existing = await db.fetch_one("SELECT id FROM rmm_device_inventory WHERE agent_id = %s", (agent_id,))
    if existing:
        return int(existing["id"])
    now = _utcnow()
    if db.is_sqlite():
        row_id = await db.execute_returning_lastrowid(
            "INSERT OR IGNORE INTO rmm_device_inventory (agent_id, company_id, asset_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (agent_id, agent.get("company_id"), agent.get("asset_id"), now, now),
        )
        if row_id:
            return int(row_id)
    else:
        try:
            row_id = await db.execute_returning_lastrowid(
                "INSERT INTO rmm_device_inventory (agent_id, company_id, asset_id, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id), company_id = VALUES(company_id), "
                "asset_id = VALUES(asset_id), updated_at = VALUES(updated_at)",
                (agent_id, agent.get("company_id"), agent.get("asset_id"), now, now),
            )
            if row_id:
                return int(row_id)
        except Exception:
            row_id = await db.fetch_one("SELECT id FROM rmm_device_inventory WHERE agent_id = %s", (agent_id,))
            if row_id:
                return int(row_id["id"])
    existing = await db.fetch_one("SELECT id FROM rmm_device_inventory WHERE agent_id = %s", (agent_id,))
    if existing:
        return int(existing["id"])
    raise RuntimeError(f"Unable to create or reuse an inventory row for agent {agent_id}")


async def save_snapshot(agent: dict[str, Any], summary: dict[str, Any], details: dict[str, Any],
                        software: list[dict[str, Any]], *, collected_at: datetime) -> None:
    """Replace the agent's snapshot and software list."""

    row_id = await _ensure_row(agent)
    now = _utcnow()
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
    agent_id = int(agent["id"])
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
