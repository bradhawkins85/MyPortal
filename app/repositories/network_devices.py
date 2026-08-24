"""Persistence for tray-agent network discovery."""

from __future__ import annotations

from typing import Any
from app.core.database import db


async def list_for_company(company_id: int) -> list[dict[str, Any]]:
    devices = list(
        await db.fetch_all(
            """SELECT nd.*, COALESCE(mv.vendor, nd.vendor) AS mac_vendor,
                  a.name AS matched_asset_name, td.hostname AS scanner_hostname,
                  td.asset_id AS scanner_asset_id, scanner_asset.name AS scanner_asset_name,
                  dt.name AS device_type_name,
                  (SELECT GROUP_CONCAT(dtv.device_type_id ORDER BY dtv.device_type_id)
                   FROM network_device_type_vendors dtv
                   WHERE LOWER(dtv.mac_vendor) = LOWER(COALESCE(mv.vendor, nd.vendor)))
                    AS recommended_device_type_ids
           FROM network_devices nd
           LEFT JOIN assets a ON a.id = nd.matched_asset_id
           JOIN tray_devices td ON td.id = nd.scanner_tray_device_id
           LEFT JOIN assets scanner_asset ON scanner_asset.id = td.asset_id
           LEFT JOIN network_device_types dt ON dt.id = nd.device_type_id
           LEFT JOIN mac_vendors mv ON mv.oui_prefix =
             SUBSTRING(UPPER(REPLACE(REPLACE(REPLACE(nd.mac_address, ':', ''), '-', ''), '.', '')), 1, 6)
           WHERE nd.company_id = %s ORDER BY nd.last_seen_at DESC, nd.ip_address""",
            (company_id,),
        )
        or []
    )
    for device in devices:
        raw_ids = device.get("recommended_device_type_ids") or ""
        device["recommended_device_type_ids"] = {
            int(value) for value in str(raw_ids).split(",") if value
        }
    return devices


async def get_for_company(device_id: int, company_id: int) -> dict[str, Any] | None:
    """Return one company-owned device with its resolved type and vendor."""
    return await db.fetch_one(
        """SELECT nd.*, dt.name AS device_type_name,
                  COALESCE(mv.vendor, nd.vendor) AS mac_vendor
           FROM network_devices nd
           LEFT JOIN network_device_types dt ON dt.id = nd.device_type_id
           LEFT JOIN mac_vendors mv ON mv.oui_prefix =
             SUBSTRING(UPPER(REPLACE(REPLACE(REPLACE(nd.mac_address, ':', ''), '-', ''), '.', '')), 1, 6)
           WHERE nd.id=%s AND nd.company_id=%s""",
        (device_id, company_id),
    )


async def list_device_types() -> list[dict[str, Any]]:
    return list(
        await db.fetch_all(
            """SELECT dt.id, dt.name, dt.auto_assign,
                      GROUP_CONCAT(dtv.mac_vendor ORDER BY dtv.mac_vendor SEPARATOR '\n') AS mac_vendors
               FROM network_device_types dt
               LEFT JOIN network_device_type_vendors dtv ON dtv.device_type_id=dt.id
               GROUP BY dt.id, dt.name, dt.auto_assign ORDER BY dt.name"""
        )
        or []
    )


async def create_device_type(
    name: str, mac_vendors: list[str] | None = None, auto_assign: bool = False
) -> None:
    device_type_id = await db.execute_returning_lastrowid(
        """INSERT INTO network_device_types (name, auto_assign) VALUES (%s, %s)
           ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id), auto_assign=VALUES(auto_assign)""",
        (name, 1 if auto_assign else 0),
    )
    await _replace_device_type_vendors(device_type_id, mac_vendors or [])
    if auto_assign:
        await _auto_assign_existing_devices(device_type_id)


async def update_device_type(
    device_type_id: int, name: str, mac_vendors: list[str], auto_assign: bool
) -> None:
    await db.execute(
        "UPDATE network_device_types SET name=%s, auto_assign=%s WHERE id=%s",
        (name, 1 if auto_assign else 0, device_type_id),
    )
    await _replace_device_type_vendors(device_type_id, mac_vendors)
    if auto_assign:
        await _auto_assign_existing_devices(device_type_id)


async def _replace_device_type_vendors(
    device_type_id: int, mac_vendors: list[str]
) -> None:
    await db.execute(
        "DELETE FROM network_device_type_vendors WHERE device_type_id=%s",
        (device_type_id,),
    )
    for vendor in mac_vendors:
        await db.execute(
            "INSERT INTO network_device_type_vendors (device_type_id, mac_vendor) VALUES (%s,%s)",
            (device_type_id, vendor),
        )


async def _auto_assign_existing_devices(device_type_id: int) -> None:
    """Assign the type only to devices that have never been classified.

    A device's current type is authoritative regardless of whether it was selected
    by a user or by an earlier automatic assignment.  In particular, changing a
    vendor mapping must not reclassify devices that already have a type.
    """
    await db.execute(
        """UPDATE network_devices nd
           LEFT JOIN mac_vendors mv ON mv.oui_prefix =
             SUBSTRING(UPPER(REPLACE(REPLACE(REPLACE(nd.mac_address, ':', ''), '-', ''), '.', '')), 1, 6)
           JOIN network_device_type_vendors dtv
             ON dtv.device_type_id=%s
             AND LOWER(dtv.mac_vendor)=LOWER(COALESCE(mv.vendor, nd.vendor))
           SET nd.device_type_id=%s WHERE nd.device_type_id IS NULL""",
        (device_type_id, device_type_id),
    )


async def delete_device_type(device_type_id: int) -> None:
    await db.execute("DELETE FROM network_device_types WHERE id=%s", (device_type_id,))


async def update_device(
    device_id: int,
    company_id: int,
    state: str,
    device_type_id: int | None,
    description: str | None,
    agent_not_required: bool,
) -> None:
    await db.execute(
        """UPDATE network_devices
           SET state=%s, device_type_id=%s, description=%s, agent_not_required=%s
           WHERE id=%s AND company_id=%s""",
        (
            state,
            device_type_id,
            description,
            1 if agent_not_required else 0,
            device_id,
            company_id,
        ),
    )


async def bulk_update_devices(
    device_ids: list[int],
    company_id: int,
    *,
    state: str | None = None,
    device_type_id: int | None = None,
    clear_device_type: bool = False,
    description: str | None = None,
    update_description: bool = False,
    agent_not_required: bool | None = None,
) -> None:
    """Apply only the requested fields to company-owned discovered devices."""
    if not device_ids:
        return

    assignments: list[str] = []
    values: list[Any] = []
    if state is not None:
        assignments.append("state=%s")
        values.append(state)
    if device_type_id is not None or clear_device_type:
        assignments.append("device_type_id=%s")
        values.append(device_type_id)
    if update_description:
        assignments.append("description=%s")
        values.append(description)
    if agent_not_required is not None:
        assignments.append("agent_not_required=%s")
        values.append(1 if agent_not_required else 0)
    if not assignments:
        return

    placeholders = ",".join("%s" for _ in device_ids)
    await db.execute(
        f"UPDATE network_devices SET {', '.join(assignments)} "
        f"WHERE company_id=%s AND id IN ({placeholders})",
        tuple(values + [company_id, *device_ids]),
    )


async def register_scanned_subnets(
    company_id: int, scanner_id: int, subnets: list[str]
) -> set[str]:
    """Record subnet baselines and return the subnets never scanned before."""
    new_subnets: set[str] = set()
    for subnet in subnets:
        existing = await db.fetch_one(
            "SELECT id FROM network_scan_subnets WHERE company_id=%s AND subnet=%s",
            (company_id, subnet),
        )
        if existing:
            continue
        await db.execute(
            """INSERT IGNORE INTO network_scan_subnets
               (company_id, scanner_tray_device_id, subnet) VALUES (%s,%s,%s)""",
            (company_id, scanner_id, subnet),
        )
        new_subnets.add(subnet)
    return new_subnets


async def list_scanners(company_id: int) -> list[dict[str, Any]]:
    return list(
        await db.fetch_all(
            """SELECT td.id, td.device_uid, td.hostname, td.asset_id, td.network_scanner_enabled,
                  td.network_scan_interval_minutes, td.network_scan_wan_cidrs,
                  td.network_scan_local_cidrs, td.last_seen_utc, a.name AS asset_name
           FROM tray_devices td LEFT JOIN assets a ON a.id = td.asset_id
           WHERE td.company_id = %s AND td.status = 'active' AND td.asset_id IS NOT NULL
           ORDER BY COALESCE(a.name, td.hostname)""",
            (company_id,),
        )
        or []
    )


async def configure_scanner(
    device_id: int,
    company_id: int,
    enabled: bool,
    interval: int,
    wan_cidrs: list[str] | None = None,
    local_cidrs: list[str] | None = None,
) -> None:
    await db.execute(
        """UPDATE tray_devices SET network_scanner_enabled=%s, network_scan_interval_minutes=%s,
           network_scan_wan_cidrs=%s, network_scan_local_cidrs=%s
           WHERE id=%s AND company_id=%s AND status='active'""",
        (
            1 if enabled else 0,
            interval,
            "\n".join(wan_cidrs or []),
            "\n".join(local_cidrs or []),
            device_id,
            company_id,
        ),
    )


async def upsert_scan(
    company_id: int, scanner_id: int, wan_ip: str, hosts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    newly_discovered: list[dict[str, Any]] = []
    for host in hosts:
        mac = host.get("mac_address") or None
        matched = None
        if mac:
            matched_row = await db.fetch_one(
                "SELECT id FROM assets WHERE company_id=%s AND "
                "FIND_IN_SET(%s, REPLACE(UPPER(REPLACE(mac_address, '-', ':')), ' ', '')) > 0 LIMIT 1",
                (company_id, mac),
            )
            matched = matched_row.get("id") if matched_row else None
        # MAC is the stable identity across locations. For hosts without one,
        # include the WAN address so identical private IPs on different networks
        # do not overwrite each other.
        existing = await db.fetch_one(
            "SELECT id FROM network_devices WHERE company_id=%s AND "
            + (
                "mac_address=%s"
                if mac
                else "mac_address IS NULL AND wan_ip=%s AND ip_address=%s"
            )
            + " LIMIT 1",
            ((company_id, mac) if mac else (company_id, wan_ip, host["ip_address"])),
        )
        values = (
            scanner_id,
            wan_ip,
            host["ip_address"],
            mac,
            host.get("hostname"),
            host.get("vendor"),
            host.get("os_details"),
            host.get("open_ports"),
            matched,
        )
        if existing:
            await db.execute(
                """UPDATE network_devices SET scanner_tray_device_id=%s, wan_ip=%s, ip_address=%s, mac_address=%s,
                   hostname=%s, vendor=%s, os_details=%s, open_ports=%s, matched_asset_id=%s,
                   state=CASE WHEN %s IS NOT NULL THEN 'Known' ELSE state END,
                   last_seen_at=CURRENT_TIMESTAMP WHERE id=%s""",
                values + (matched, existing["id"]),
            )
            device_id = existing["id"]
        else:
            device_id = await db.execute_returning_lastrowid(
                """INSERT INTO network_devices (company_id, scanner_tray_device_id, wan_ip, ip_address, mac_address,
                   hostname, vendor, os_details, open_ports, matched_asset_id, state)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (company_id,) + values + ("Known" if matched else "New",),
            )
            newly_discovered.append(
                {
                    "id": device_id,
                    "ip_address": host["ip_address"],
                    "mac_address": mac,
                    "hostname": host.get("hostname"),
                    "vendor": host.get("vendor"),
                    "matched_asset_id": matched,
                }
            )
        await _auto_assign_device_type(device_id)
    return newly_discovered


async def _auto_assign_device_type(device_id: int) -> None:
    """Classify an untyped device without replacing an existing classification."""
    await db.execute(
        """UPDATE network_devices nd
           LEFT JOIN mac_vendors mv ON mv.oui_prefix =
             SUBSTRING(UPPER(REPLACE(REPLACE(REPLACE(nd.mac_address, ':', ''), '-', ''), '.', '')), 1, 6)
           SET nd.device_type_id = (
             SELECT dtv.device_type_id
             FROM network_device_type_vendors dtv
             JOIN network_device_types dt ON dt.id=dtv.device_type_id AND dt.auto_assign=1
             WHERE LOWER(dtv.mac_vendor)=LOWER(COALESCE(mv.vendor, nd.vendor))
             ORDER BY dtv.device_type_id LIMIT 1
           )
           WHERE nd.id=%s AND nd.device_type_id IS NULL
             AND EXISTS (
               SELECT 1 FROM network_device_type_vendors dtv
               JOIN network_device_types dt ON dt.id=dtv.device_type_id AND dt.auto_assign=1
               WHERE LOWER(dtv.mac_vendor)=LOWER(COALESCE(mv.vendor, nd.vendor))
             )""",
        (device_id,),
    )
