"""Persistence for tray-agent network discovery."""

from __future__ import annotations

from typing import Any
from app.core.database import db


async def list_for_company(company_id: int) -> list[dict[str, Any]]:
    return list(
        await db.fetch_all(
            """SELECT nd.*, a.name AS matched_asset_name, td.hostname AS scanner_hostname
           FROM network_devices nd
           LEFT JOIN assets a ON a.id = nd.matched_asset_id
           JOIN tray_devices td ON td.id = nd.scanner_tray_device_id
           WHERE nd.company_id = %s ORDER BY nd.last_seen_at DESC, nd.ip_address""",
            (company_id,),
        )
        or []
    )


async def list_scanners(company_id: int) -> list[dict[str, Any]]:
    return list(
        await db.fetch_all(
            """SELECT td.id, td.hostname, td.asset_id, td.network_scanner_enabled,
                  td.network_scan_interval_minutes, td.last_seen_utc, a.name AS asset_name
           FROM tray_devices td LEFT JOIN assets a ON a.id = td.asset_id
           WHERE td.company_id = %s AND td.status = 'active' AND td.asset_id IS NOT NULL
           ORDER BY COALESCE(a.name, td.hostname)""",
            (company_id,),
        )
        or []
    )


async def configure_scanner(
    device_id: int, company_id: int, enabled: bool, interval: int
) -> None:
    await db.execute(
        """UPDATE tray_devices SET network_scanner_enabled=%s, network_scan_interval_minutes=%s
           WHERE id=%s AND company_id=%s AND status='active'""",
        (1 if enabled else 0, interval, device_id, company_id),
    )


async def upsert_scan(
    company_id: int, scanner_id: int, wan_ip: str, hosts: list[dict[str, Any]]
) -> None:
    for host in hosts:
        mac = host.get("mac_address") or None
        matched = None
        if mac:
            matched_row = await db.fetch_one(
                "SELECT id FROM assets WHERE company_id=%s AND UPPER(REPLACE(mac_address, '-', ':'))=%s LIMIT 1",
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
            (
                (company_id, mac)
                if mac
                else (company_id, wan_ip, host["ip_address"])
            ),
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
                   last_seen_at=CURRENT_TIMESTAMP WHERE id=%s""",
                values + (existing["id"],),
            )
        else:
            await db.execute(
                """INSERT INTO network_devices (company_id, scanner_tray_device_id, wan_ip, ip_address, mac_address,
                   hostname, vendor, os_details, open_ports, matched_asset_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (company_id,) + values,
            )
