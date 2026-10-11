"""Storage for pushing the RMM agent to tray devices.

Each push is an ``install_rmm_agent`` row in ``tray_command_log``, so the tray
command history shows it like any other command and its outcome lands in the
row's ``status`` and ``error``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db

INSTALL_COMMAND = "install_rmm_agent"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def get_asset_tray_device(asset_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM tray_devices WHERE asset_id = %s AND status = 'active' "
        "ORDER BY last_seen_utc DESC, id DESC LIMIT 1",
        (asset_id,),
    )


async def device_has_agent(device: dict[str, Any]) -> bool:
    """An active agent enrolled through this tray device, or reports for its asset
    (for example after the tray app was reinstalled)."""

    row = await db.fetch_one(
        "SELECT 1 AS found FROM tray_devices d WHERE d.id = %s AND EXISTS ("
        "SELECT 1 FROM rmm_agents g WHERE g.status = 'active' AND "
        "(g.tray_device_id = d.id OR (d.asset_id IS NOT NULL AND g.asset_id = d.asset_id)))",
        (int(device["id"]),),
    )
    return row is not None


async def list_company_devices_without_agent(company_id: int) -> list[dict[str, Any]]:
    """Active tray devices of a company without an agent (see :func:`device_has_agent`)."""

    rows = await db.fetch_all(
        "SELECT d.* FROM tray_devices d WHERE d.company_id = %s AND d.status = 'active' AND NOT EXISTS ("
        "SELECT 1 FROM rmm_agents g WHERE g.status = 'active' AND "
        "(g.tray_device_id = d.id OR (d.asset_id IS NOT NULL AND g.asset_id = d.asset_id))) "
        "ORDER BY d.id",
        (company_id,),
    )
    return [dict(row) for row in rows or []]


async def list_company_agent_asset_ids(company_id: int) -> set[int]:
    rows = await db.fetch_all(
        "SELECT DISTINCT asset_id FROM rmm_agents WHERE company_id = %s AND status = 'active' "
        "AND asset_id IS NOT NULL",
        (company_id,),
    )
    return {int(row["asset_id"]) for row in rows or []}


async def latest_install_command(device_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM tray_command_log WHERE device_id = %s AND command = %s ORDER BY id DESC LIMIT 1",
        (device_id, INSTALL_COMMAND),
    )


async def create_command(device_id: int, command: str, *, user_id: int | None, payload_json: str) -> int:
    last_id = await db.execute_returning_lastrowid(
        "INSERT INTO tray_command_log (device_id, command, payload_json, initiated_by_user_id, status, created_at) "
        "VALUES (%s, %s, %s, %s, 'queued', %s)",
        (device_id, command, payload_json, user_id, _utcnow()),
    )
    return int(last_id or 0)


async def create_install_command(device_id: int, *, user_id: int | None, payload_json: str) -> int:
    return await create_command(device_id, INSTALL_COMMAND, user_id=user_id, payload_json=payload_json)


async def queued_command(device_id: int, command: str) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM tray_command_log WHERE device_id = %s AND command = %s AND status = 'queued' "
        "ORDER BY id DESC LIMIT 1",
        (device_id, command),
    )


async def get_tray_device(device_id: int) -> dict[str, Any] | None:
    return await db.fetch_one("SELECT * FROM tray_devices WHERE id = %s", (device_id,))


async def get_install_command(command_id: int, device_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM tray_command_log WHERE id = %s AND device_id = %s AND command = %s",
        (command_id, device_id, INSTALL_COMMAND),
    )


async def set_command_status(command_id: int, status: str, *, error: str | None = None) -> None:
    await db.execute(
        "UPDATE tray_command_log SET status = %s, error = %s, delivered_at = COALESCE(delivered_at, %s) "
        "WHERE id = %s",
        (status, error, _utcnow(), command_id),
    )
