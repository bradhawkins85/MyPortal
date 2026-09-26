"""Company-scoped IP address management and rack documentation."""
from __future__ import annotations

import ipaddress
from typing import Any

from app.core.database import db

ADDRESS_STATES = {"available", "reserved", "assigned", "dhcp", "deprecated"}


async def overview(company_id: int) -> dict[str, list[dict[str, Any]]]:
    networks = list(await db.fetch_all(
        """SELECT n.*, COUNT(i.id) address_count
           FROM ip_networks n LEFT JOIN ip_addresses i ON i.network_id=n.id
           WHERE n.company_id=%s GROUP BY n.id ORDER BY n.name""", (company_id,)) or [])
    addresses = list(await db.fetch_all(
        """SELECT i.*, n.name network_name, a.name asset_name,
                  GROUP_CONCAT(d.name ORDER BY d.name SEPARATOR ', ') dns_names
           FROM ip_addresses i JOIN ip_networks n ON n.id=i.network_id
           LEFT JOIN assets a ON a.id=i.asset_id
           LEFT JOIN ip_dns_names d ON d.ip_address_id=i.id
           WHERE i.company_id=%s GROUP BY i.id ORDER BY i.address""", (company_id,)) or [])
    racks = list(await db.fetch_all(
        """SELECT r.*, COUNT(e.id) equipment_count
           FROM racks r LEFT JOIN rack_equipment e ON e.rack_id=r.id
           WHERE r.company_id=%s GROUP BY r.id ORDER BY r.name""", (company_id,)) or [])
    equipment = list(await db.fetch_all(
        """SELECT e.*, r.name rack_name, r.unit_count, a.name asset_name
           FROM rack_equipment e JOIN racks r ON r.id=e.rack_id
           JOIN assets a ON a.id=e.asset_id WHERE e.company_id=%s
           ORDER BY r.name, e.start_unit DESC""", (company_id,)) or [])
    return {"networks": networks, "addresses": addresses, "racks": racks, "equipment": equipment}


async def create_network(company_id: int, name: str, cidr: str, description: str | None) -> int:
    canonical = str(ipaddress.ip_network(cidr, strict=False))
    return await db.execute_returning_lastrowid(
        "INSERT INTO ip_networks (company_id,name,cidr,description) VALUES (%s,%s,%s,%s)",
        (company_id, name, canonical, description))


async def create_address(company_id: int, network_id: int, address: str, state: str,
                         asset_id: int | None, dns_names: list[str], notes: str | None) -> int:
    if state not in ADDRESS_STATES:
        raise ValueError("Invalid address state")
    parsed = ipaddress.ip_address(address)
    network = await db.fetch_one(
        "SELECT cidr FROM ip_networks WHERE id=%s AND company_id=%s", (network_id, company_id))
    if not network or parsed not in ipaddress.ip_network(network["cidr"]):
        raise ValueError("Address is outside the selected network")
    if asset_id is not None and not await db.fetch_one(
        "SELECT id FROM assets WHERE id=%s AND company_id=%s", (asset_id, company_id)):
        raise ValueError("Asset does not belong to this company")
    row_id = await db.execute_returning_lastrowid(
        """INSERT INTO ip_addresses (company_id,network_id,address,state,asset_id,notes)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (company_id, network_id, str(parsed), state, asset_id, notes))
    for name in dict.fromkeys(item.strip().lower().rstrip(".") for item in dns_names if item.strip()):
        await db.execute("INSERT INTO ip_dns_names (ip_address_id,name) VALUES (%s,%s)", (row_id, name))
    return row_id


async def create_rack(company_id: int, name: str, location: str | None, unit_count: int) -> int:
    if not 1 <= unit_count <= 100:
        raise ValueError("Rack size must be between 1 and 100 units")
    return await db.execute_returning_lastrowid(
        "INSERT INTO racks (company_id,name,location,unit_count) VALUES (%s,%s,%s,%s)",
        (company_id, name, location, unit_count))


async def place_asset(company_id: int, rack_id: int, asset_id: int, start_unit: int,
                      unit_height: int, face: str, notes: str | None) -> int:
    if face not in {"front", "rear"} or unit_height < 1:
        raise ValueError("Invalid rack position")
    rack = await db.fetch_one("SELECT unit_count FROM racks WHERE id=%s AND company_id=%s", (rack_id, company_id))
    asset = await db.fetch_one("SELECT id FROM assets WHERE id=%s AND company_id=%s", (asset_id, company_id))
    if not rack or not asset or start_unit < 1 or start_unit + unit_height - 1 > int(rack["unit_count"]):
        raise ValueError("Rack, asset, or occupied units are invalid")
    units = range(start_unit, start_unit + unit_height)
    placeholders = ",".join("%s" for _ in units)
    conflict = await db.fetch_one(
        "SELECT unit_number FROM rack_equipment_units WHERE rack_id=%s AND face=%s AND unit_number IN (" + placeholders + ")",
        (rack_id, face, *units))
    if conflict:
        raise ValueError(f"Rack unit {conflict['unit_number']} is already occupied")
    equipment_id = await db.execute_returning_lastrowid(
        """INSERT INTO rack_equipment
           (company_id,rack_id,asset_id,start_unit,unit_height,face,notes)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (company_id, rack_id, asset_id, start_unit, unit_height, face, notes))
    try:
        for unit in units:
            await db.execute(
                "INSERT INTO rack_equipment_units (equipment_id,rack_id,unit_number,face) VALUES (%s,%s,%s,%s)",
                (equipment_id, rack_id, unit, face))
    except Exception as exc:
        # The unique unit key is the final race-safe guard. Remove the parent
        # (and any units cascaded from it) if another request won the slot.
        await db.execute("DELETE FROM rack_equipment WHERE id=%s", (equipment_id,))
        raise ValueError("One or more rack units are already occupied") from exc
    return equipment_id


async def delete_record(table: str, record_id: int, company_id: int) -> None:
    allowed = {"ip_networks", "ip_addresses", "racks", "rack_equipment"}
    if table not in allowed:
        raise ValueError("Invalid record type")
    await db.execute("DELETE FROM " + table + " WHERE id=%s AND company_id=%s", (record_id, company_id))
