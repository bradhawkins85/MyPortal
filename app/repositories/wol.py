"""Repository for Wake-on-LAN (WoL) attempts and their network lookups.

All access to ``wol_attempts`` flows through this module, along with the
network queries used to (a) resolve the subnet a target asset lives on and
(b) discover the on-network tray agents that can broadcast a WoL magic
packet. Functions accept and return plain ``dict`` objects, matching the
style used by other repositories.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db


def _utcnow() -> datetime:
    """UTC wall-clock time as a naive datetime, matching the rest of the app.

    Production stores all timestamps as UTC (the DB connection sets
    ``time_zone = '+00:00'``), so ``datetime.now()`` (local time) must not be
    used here.
    """

    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# wol_attempts CRUD
# ---------------------------------------------------------------------------


async def create_attempt(
    *,
    company_id: int,
    asset_id: int | None,
    target_device_id: int | None,
    target_mac: str,
    subnet: str | None,
    trigger_type: str = "manual",
    initiated_by_user_id: int | None = None,
    sender_device_ids: list[int] | None = None,
    status: str = "pending",
    check_deadline: datetime | None = None,
) -> int:
    placeholder = "?" if db.is_sqlite() else "%s"
    now = _utcnow()
    values = [
        company_id,
        asset_id,
        target_device_id,
        target_mac,
        subnet,
        trigger_type,
        initiated_by_user_id,
        json.dumps(sender_device_ids or []),
        status,
        check_deadline,
        now,
        now,
    ]
    query = (
        f"INSERT INTO wol_attempts "
        f"(company_id, asset_id, target_device_id, target_mac, subnet, trigger_type, "
        f"initiated_by_user_id, sender_device_ids, status, check_deadline, created_at, updated_at) "
        f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, "
        f"{placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})"
    )
    return int(await db.execute_returning_lastrowid(query, tuple(values)))


async def get_attempt(attempt_id: int) -> dict[str, Any] | None:
    placeholder = "?" if db.is_sqlite() else "%s"
    row = await db.fetch_one(
        f"SELECT * FROM wol_attempts WHERE id = {placeholder}", (attempt_id,)
    )
    return dict(row) if row else None


async def list_attempts_by_asset(asset_id: int, limit: int = 20) -> list[dict[str, Any]]:
    placeholder = "?" if db.is_sqlite() else "%s"
    limit_value = int(limit)
    rows = await db.fetch_all(
        f"SELECT * FROM wol_attempts WHERE asset_id = {placeholder} "
        f"ORDER BY created_at DESC, id DESC LIMIT {placeholder}",
        (asset_id, limit_value),
    )
    return [dict(r) for r in rows]


async def list_pending_attempts() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM wol_attempts WHERE status IN ('pending', 'awaiting') "
        "ORDER BY created_at ASC, id ASC LIMIT 200"
    )
    return [dict(r) for r in rows]


async def _update_attempt(
    attempt_id: int, *, sets: list[str], values: list[Any]
) -> None:
    placeholder = "?" if db.is_sqlite() else "%s"
    now = _utcnow()
    set_clause = ", ".join(sets)
    sql = (
        f"UPDATE wol_attempts SET {set_clause}, updated_at = {placeholder} "
        f"WHERE id = {placeholder}"
    )
    await db.execute(sql, tuple(values + [now, attempt_id]))


async def mark_attempt_sent(attempt_id: int, device_id: int) -> None:
    """Record that ``device_id`` broadcast the WoL packet for this attempt.

    Appends to the ``sender_device_ids`` JSON array (de-duplicated) and moves
    the attempt from ``pending`` to ``awaiting`` once at least one agent has
    sent.
    """

    attempt = await get_attempt(attempt_id)
    if not attempt:
        return
    senders: list[int] = []
    raw = attempt.get("sender_device_ids")
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            senders = [
                int(x)
                for x in parsed
                if isinstance(x, (int, str)) and str(x).lstrip("-").isdigit()
            ]
        except (ValueError, TypeError):
            senders = []
    if device_id not in senders:
        senders.append(device_id)
    new_status = "awaiting" if attempt.get("status") == "pending" else attempt.get("status")
    now = _utcnow()
    placeholder = "?" if db.is_sqlite() else "%s"
    await db.execute(
        f"UPDATE wol_attempts SET sender_device_ids = {placeholder}, "
        f"status = {placeholder}, updated_at = {placeholder} WHERE id = {placeholder}",
        (json.dumps(senders), new_status, now, attempt_id),
    )


async def mark_attempt_awake(attempt_id: int, woken_via: str) -> None:
    await _update_attempt(
        attempt_id,
        sets=["status = ?", "woken_at = ?", "woken_via = ?", "error = ?"],
        values=["awake", _utcnow(), woken_via, None],
    )


async def mark_attempt_finished(
    attempt_id: int, *, status_value: str, error: str | None = None
) -> None:
    await _update_attempt(
        attempt_id, sets=["status = ?", "error = ?"], values=[status_value, error]
    )


async def record_command_error(attempt_id: int, error: str | None) -> None:
    """Store a diagnostic without changing the pending/awaiting state."""

    await _update_attempt(attempt_id, sets=["error = ?"], values=[error])


async def asset_stats(asset_id: int) -> dict[str, Any]:
    """Aggregate WoL success for an asset so techs can gauge likelihood.

    Returns ``total``, ``success`` (awake), and ``last_success_at``.
    """

    placeholder = "?" if db.is_sqlite() else "%s"
    row = await db.fetch_one(
        f"SELECT COUNT(*) AS total, "
        f"SUM(CASE WHEN status = 'awake' THEN 1 ELSE 0 END) AS success, "
        f"MAX(CASE WHEN status = 'awake' THEN woken_at ELSE NULL END) AS last_success_at "
        f"FROM wol_attempts WHERE asset_id = {placeholder}",
        (asset_id,),
    )
    row = dict(row) if row else {}
    return {
        "total": int(row.get("total") or 0),
        "success": int(row.get("success") or 0),
        "last_success_at": row.get("last_success_at"),
    }


# ---------------------------------------------------------------------------
# Network lookups
# ---------------------------------------------------------------------------


async def get_target_subnet_cidr(asset_id: int) -> str | None:
    """Resolve the subnet (CIDR) a target asset lives on.

    Preference:
      1. IPAM: the ``ip_networks.cidr`` of an ``ip_addresses`` row assigned to
         the asset.
      2. Subnet scanners: the subnet recorded by the scanner that discovered
         the (matched) device for the asset.
    """

    placeholder = "?" if db.is_sqlite() else "%s"
    row = await db.fetch_one(
        "SELECT n.cidr AS cidr FROM ip_addresses AS a "
        "JOIN ip_networks AS n ON n.id = a.network_id "
        f"WHERE a.asset_id = {placeholder} LIMIT 1",
        (asset_id,),
    )
    if row and row.get("cidr"):
        return str(row.get("cidr"))

    row = await db.fetch_one(
        "SELECT nss.subnet AS cidr FROM network_devices AS nd "
        "JOIN network_scan_subnets AS nss "
        "  ON nss.scanner_tray_device_id = nd.scanner_tray_device_id "
        "  AND nss.company_id = nd.company_id "
        f"WHERE nd.matched_asset_id = {placeholder} LIMIT 1",
        (asset_id,),
    )
    if row and row.get("cidr"):
        return str(row.get("cidr"))
    return None


async def list_active_devices(
    company_id: int, exclude_device_id: int | None = None
) -> list[dict[str, Any]]:
    """Active tray devices in a company that report a last IP, with presence fields."""

    placeholder = "?" if db.is_sqlite() else "%s"
    rows = await db.fetch_all(
        "SELECT id, device_uid, hostname, asset_id, last_ip, last_seen_utc, ws_last_seen_utc "
        f"FROM tray_devices WHERE status = 'active' AND company_id = {placeholder} "
        f"AND last_ip IS NOT NULL AND last_ip != '' AND id != {placeholder} "
        f"ORDER BY id ASC",
        (company_id, exclude_device_id or -1),
    )
    return [dict(r) for r in rows]


async def list_subnet_scanner_devices(
    company_id: int, subnet: str
) -> list[dict[str, Any]]:
    """Tray devices registered as scanners for ``subnet`` (they sit on it)."""

    placeholder = "?" if db.is_sqlite() else "%s"
    rows = await db.fetch_all(
        "SELECT td.id, td.device_uid, td.hostname, td.asset_id, td.last_ip, "
        "td.last_seen_utc, td.ws_last_seen_utc "
        "FROM network_scan_subnets AS nss "
        "JOIN tray_devices AS td ON td.id = nss.scanner_tray_device_id "
        f"WHERE nss.company_id = {placeholder} AND nss.subnet = {placeholder} "
        f"AND td.status = 'active' ORDER BY td.id ASC",
        (company_id, subnet),
    )
    return [dict(r) for r in rows]


async def get_matched_device_mac(asset_id: int) -> str | None:
    """A MAC address recorded by a subnet scanner for the matched device."""

    placeholder = "?" if db.is_sqlite() else "%s"
    row = await db.fetch_one(
        f"SELECT mac_address FROM network_devices "
        f"WHERE matched_asset_id = {placeholder} AND mac_address IS NOT NULL "
        f"AND mac_address != '' LIMIT 1",
        (asset_id,),
    )
    return str(row.get("mac_address")) if row and row.get("mac_address") else None


