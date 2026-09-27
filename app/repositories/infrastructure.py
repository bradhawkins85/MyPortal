"""Company-scoped IP address management and rack documentation."""
from __future__ import annotations

import ipaddress
from typing import Any

from app.core.database import db
from app.services import rack_item_types

ADDRESS_STATES = {"available", "reserved", "assigned", "dhcp", "deprecated"}


def match_address_network(
    address: str, networks: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str | None]:
    """Select the unique, most-specific network containing ``address``."""
    try:
        parsed = ipaddress.ip_address(str(address).strip())
    except ValueError:
        return None, "Invalid IP address"
    matches: list[tuple[int, dict[str, Any]]] = []
    for network in networks:
        try:
            parsed_network = ipaddress.ip_network(str(network["cidr"]), strict=False)
        except (KeyError, ValueError):
            continue
        if parsed.version == parsed_network.version and parsed in parsed_network:
            matches.append((parsed_network.prefixlen, network))
    if not matches:
        return None, "No company network contains this address"
    best_prefix = max(prefix for prefix, _network in matches)
    best = [network for prefix, network in matches if prefix == best_prefix]
    if len(best) != 1:
        return None, "Multiple equally specific networks match; review required"
    return best[0], None


async def preview_discovered_addresses(
    company_id: int, devices: list[dict[str, Any]], *, use_wan: bool = False
) -> list[dict[str, Any]]:
    """Build an import preview without trusting client-supplied subnet choices."""
    networks = list(await db.fetch_all(
        "SELECT id,name,cidr FROM ip_networks WHERE company_id=%s ORDER BY name",
        (company_id,),
    ) or [])
    previews = []
    for device in devices:
        candidate = device.get("wan_ip") if use_wan else device.get("ip_address")
        network, reason = match_address_network(str(candidate or ""), networks)
        previews.append({
            "device": device,
            "candidate": str(candidate or ""),
            "network": network,
            "reason": reason,
            "source_url": f"/devices#discovered-device-{device['id']}",
            "asset_url": f"/assets/{device['matched_asset_id']}" if device.get("matched_asset_id") else None,
        })
    return previews


async def import_discovered_address(
    company_id: int, device: dict[str, Any], *, use_wan: bool = False
) -> dict[str, Any]:
    """Idempotently document one discovery while preserving manual IPAM data."""
    preview = (await preview_discovered_addresses(company_id, [device], use_wan=use_wan))[0]
    if preview["reason"]:
        return {"status": "skipped", "reason": preview["reason"], **preview}
    parsed = str(ipaddress.ip_address(preview["candidate"]))
    existing = await db.fetch_one(
        """SELECT i.*, COUNT(d.id) AS dns_count
           FROM ip_addresses i LEFT JOIN ip_dns_names d ON d.ip_address_id=i.id
           WHERE i.company_id=%s AND i.address=%s GROUP BY i.id""",
        (company_id, parsed),
    )
    asset_id = device.get("matched_asset_id")
    if existing:
        same_source = existing.get("source_network_device_id") == device.get("id")
        same_asset = existing.get("asset_id") == asset_id
        if not same_source or not same_asset:
            return {"status": "conflict", "reason": "Address already has a different assignment or source", **preview}
        return {"status": "updated", "record_id": existing["id"], **preview}
    try:
        record_id = await db.execute_returning_lastrowid(
            """INSERT INTO ip_addresses
               (company_id,network_id,address,state,asset_id,source_network_device_id)
               VALUES (%s,%s,%s,'assigned',%s,%s)""",
            (company_id, preview["network"]["id"], parsed, asset_id, device["id"]),
        )
    except Exception:
        # The company/address unique key is the race-safe final guard. Re-read
        # rather than overwriting whichever request or technician won.
        return {"status": "conflict", "reason": "Address was documented concurrently; review required", **preview}
    return {"status": "created", "record_id": record_id, **preview}


async def get_record(table: str, company_id: int, record_id: int) -> dict[str, Any] | None:
    if table not in {"ip_networks", "racks"}:
        raise ValueError("Invalid infrastructure record type")
    return await db.fetch_one(
        "SELECT * FROM " + table + " WHERE id=%s AND company_id=%s",
        (record_id, company_id),
    )


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
        """SELECT e.*, r.name rack_name, r.unit_count, r.depth_mm, a.name asset_name,
                  a.type asset_type, a.serial_number asset_serial, a.status asset_status
           FROM rack_equipment e JOIN racks r ON r.id=e.rack_id
           LEFT JOIN assets a ON a.id=e.asset_id WHERE e.company_id=%s
        ORDER BY r.name, e.start_unit DESC""", (company_id,)) or [])
    ports = list(await db.fetch_all(
        """SELECT p.*, a.name asset_name FROM rack_equipment_ports p
           LEFT JOIN assets a ON a.id=p.asset_id WHERE p.company_id=%s
           ORDER BY p.equipment_id, p.port_number""", (company_id,)) or [])
    ports_by_equipment: dict[int, list[dict[str, Any]]] = {}
    for port in ports:
        ports_by_equipment.setdefault(port["equipment_id"], []).append(port)
    for item in equipment:
        item["ports"] = ports_by_equipment.get(item["id"], [])
    reservations = list(await db.fetch_all(
        """SELECT q.*, r.name rack_name FROM rack_reservations q
           JOIN racks r ON r.id=q.rack_id WHERE q.company_id=%s
           ORDER BY r.name, q.start_unit DESC""", (company_id,)) or [])
    for rack in racks:
        rack_equipment = [item for item in equipment if item["rack_id"] == rack["id"]]
        rack_reservations = [item for item in reservations if item["rack_id"] == rack["id"]]
        capacity = int(rack["unit_count"]) * 3 * 2
        occupied = sum(int(item["unit_height"]) * int(item["width_lanes"]) *
                       (2 if item["depth_mode"] == "full" else 1) for item in rack_equipment)
        reserved = sum(int(item["unit_height"]) * int(item["width_lanes"]) *
                       (2 if item["depth_mode"] == "full" else 1) for item in rack_reservations)
        rack["occupied_percent"] = round(occupied * 100 / capacity) if capacity else 0
        rack["reserved_percent"] = round(reserved * 100 / capacity) if capacity else 0
        draws = [item.get("power_draw_watts") for item in rack_equipment]
        rack["recorded_power_draw_watts"] = sum(value for value in draws if value is not None)
        rack["has_recorded_power_draw"] = any(value is not None for value in draws)
    return {"networks": networks, "addresses": addresses, "racks": racks,
            "equipment": equipment, "reservations": reservations}


async def for_asset(company_id: int, asset_id: int) -> dict[str, list[dict[str, Any]]]:
    """Return company-scoped IP assignments and rack placements for an asset."""
    addresses = list(await db.fetch_all(
        """SELECT i.id, i.address, i.state, n.name network_name
           FROM ip_addresses i JOIN ip_networks n ON n.id=i.network_id
           WHERE i.company_id=%s AND i.asset_id=%s ORDER BY i.address""",
        (company_id, asset_id)) or [])
    placements = list(await db.fetch_all(
        """SELECT e.id, e.rack_id, e.start_unit, e.unit_height, e.face, r.name rack_name
           FROM rack_equipment e JOIN racks r ON r.id=e.rack_id
           WHERE e.company_id=%s AND e.asset_id=%s ORDER BY r.name, e.face""",
        (company_id, asset_id)) or [])
    return {"addresses": addresses, "placements": placements}


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


async def create_rack(company_id: int, name: str, location: str | None, unit_count: int,
                      depth_mm: int = 1000) -> int:
    if not 1 <= unit_count <= 100:
        raise ValueError("Rack size must be between 1 and 100 units")
    if not 100 <= depth_mm <= 5000:
        raise ValueError("Rack depth must be between 100 and 5000 mm")
    return await db.execute_returning_lastrowid(
        "INSERT INTO racks (company_id,name,location,unit_count,depth_mm) VALUES (%s,%s,%s,%s,%s)",
        (company_id, name, location, unit_count, depth_mm))


def _item_name(name: str | None, asset: dict[str, Any] | None, item_type: str) -> str:
    """Return the display name for a rack item, falling back to its asset or type."""
    clean = (name or "").strip()
    if clean:
        return clean
    asset_name = str((asset or {}).get("name") or "").strip()
    return asset_name[:191] or rack_item_types.get(item_type).label


async def place_asset(company_id: int, rack_id: int, asset_id: int | None, start_unit: int,
                      unit_height: int, face: str, notes: str | None,
                      width_lanes: int = 3, start_lane: int = 1,
                      depth_mode: str = "half", power_draw_watts: int | None = None,
                      item_type: str = "server", name: str | None = None,
                      port_count: int = 0) -> int:
    item_type = rack_item_types.normalise(item_type)
    if (face not in {"front", "rear"} or depth_mode not in {"half", "full"}
            or unit_height < 1 or width_lanes not in {1, 2, 3}
            or start_lane < 1 or start_lane + width_lanes - 1 > 3
            or port_count < 0 or port_count > 1000
            or (power_draw_watts is not None and power_draw_watts < 0)):
        raise ValueError("Invalid rack item or position")
    if len((name or "").strip()) > 191:
        raise ValueError("Rack item name is too long")
    rack = await db.fetch_one("SELECT unit_count FROM racks WHERE id=%s AND company_id=%s", (rack_id, company_id))
    asset = None
    if asset_id is not None:
        asset = await db.fetch_one("SELECT id, name FROM assets WHERE id=%s AND company_id=%s", (asset_id, company_id))
    clean_name = _item_name(name, asset, item_type)
    if not rack or (asset_id is not None and not asset) or start_unit < 1 or start_unit + unit_height - 1 > int(rack["unit_count"]):
        raise ValueError("Rack, asset, or occupied units are invalid")
    units = list(range(start_unit, start_unit + unit_height))
    lanes = list(range(start_lane, start_lane + width_lanes))
    faces = ["front", "rear"] if depth_mode == "full" else [face]
    slot_conditions = " OR ".join("(unit_number=%s AND face=%s AND lane=%s)" for _ in units for _face in faces for _lane in lanes)
    slot_params = tuple(value for unit in units for slot_face in faces for lane in lanes for value in (unit, slot_face, lane))
    for table in ("rack_equipment_slots", "rack_reservation_slots"):
        conflict = await db.fetch_one("SELECT unit_number FROM " + table + " WHERE rack_id=%s AND (" + slot_conditions + ")", (rack_id, *slot_params))
        if conflict:
            raise ValueError(f"Rack unit {conflict['unit_number']} is already occupied or reserved")
    equipment_id = await db.execute_returning_lastrowid(
        """INSERT INTO rack_equipment
           (company_id,rack_id,asset_id,start_unit,unit_height,face,notes,width_lanes,start_lane,depth_mode,power_draw_watts,item_type,name,port_count)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (company_id, rack_id, asset_id, start_unit, unit_height, face, notes, width_lanes,
         start_lane, depth_mode, power_draw_watts, item_type, clean_name, port_count))
    try:
        for unit in units:
            for slot_face in faces:
                for lane in lanes:
                    await db.execute("INSERT INTO rack_equipment_slots (equipment_id,rack_id,unit_number,face,lane) VALUES (%s,%s,%s,%s,%s)", (equipment_id, rack_id, unit, slot_face, lane))
        for port_number in range(1, port_count + 1):
            await db.execute("INSERT INTO rack_equipment_ports (company_id,equipment_id,port_number) VALUES (%s,%s,%s)", (company_id, equipment_id, port_number))
    except Exception as exc:
        await db.execute("DELETE FROM rack_equipment WHERE id=%s", (equipment_id,))
        raise ValueError("One or more rack units are already occupied") from exc
    return equipment_id


async def link_equipment_port(company_id: int, equipment_id: int, port_number: int,
                              asset_id: int | None) -> None:
    port = await db.fetch_one(
        "SELECT id FROM rack_equipment_ports WHERE company_id=%s AND equipment_id=%s AND port_number=%s",
        (company_id, equipment_id, port_number))
    if not port:
        raise ValueError("Rack item port not found")
    if asset_id is not None and not await db.fetch_one(
            "SELECT id FROM assets WHERE id=%s AND company_id=%s", (asset_id, company_id)):
        raise ValueError("Asset does not belong to this company")
    await db.execute("UPDATE rack_equipment_ports SET asset_id=%s WHERE id=%s AND company_id=%s",
                     (asset_id, port["id"], company_id))


async def update_rack_equipment(company_id: int, equipment_id: int, name: str | None,
                                item_type: str, asset_id: int | None,
                                power_draw_watts: int | None, notes: str | None) -> None:
    """Update a documented item without disturbing its placement or port links."""
    item_type = rack_item_types.normalise(item_type)
    if len((name or "").strip()) > 191:
        raise ValueError("Rack item name is too long")
    if power_draw_watts is not None and power_draw_watts < 0:
        raise ValueError("Power draw cannot be negative")
    item = await db.fetch_one(
        "SELECT id, port_count FROM rack_equipment WHERE id=%s AND company_id=%s",
        (equipment_id, company_id))
    if not item:
        raise ValueError("Rack item not found")
    asset = None
    if asset_id is not None:
        asset = await db.fetch_one(
            "SELECT id, name FROM assets WHERE id=%s AND company_id=%s", (asset_id, company_id))
        if not asset:
            raise ValueError("Asset does not belong to this company")
    clean_name = _item_name(name, asset, item_type)
    await db.execute(
        """UPDATE rack_equipment SET name=%s,item_type=%s,asset_id=%s,
                  power_draw_watts=%s,notes=%s WHERE id=%s AND company_id=%s""",
        (clean_name, item_type, asset_id, power_draw_watts, notes, equipment_id, company_id))


async def update_rack_reservation(company_id: int, reservation_id: int,
                                  label: str | None, owner: str | None,
                                  notes: str | None) -> None:
    reservation = await db.fetch_one(
        "SELECT id FROM rack_reservations WHERE id=%s AND company_id=%s",
        (reservation_id, company_id))
    if not reservation:
        raise ValueError("Reservation not found")
    await db.execute(
        "UPDATE rack_reservations SET label=%s,owner=%s,notes=%s WHERE id=%s AND company_id=%s",
        (label, owner, notes, reservation_id, company_id))


async def reserve_space(company_id: int, rack_id: int, start_unit: int, unit_height: int,
                        face: str, width_lanes: int, start_lane: int, depth_mode: str,
                        label: str | None, owner: str | None, notes: str | None) -> int:
    if (face not in {"front", "rear"} or depth_mode not in {"half", "full"}
            or unit_height < 1 or width_lanes not in {1, 2, 3}
            or start_lane < 1 or start_lane + width_lanes - 1 > 3):
        raise ValueError("Invalid rack reservation")
    rack = await db.fetch_one(
        "SELECT unit_count FROM racks WHERE id=%s AND company_id=%s", (rack_id, company_id))
    if not rack or start_unit < 1 or start_unit + unit_height - 1 > int(rack["unit_count"]):
        raise ValueError("Rack or reserved units are invalid")
    units = range(start_unit, start_unit + unit_height)
    lanes = range(start_lane, start_lane + width_lanes)
    faces = ["front", "rear"] if depth_mode == "full" else [face]
    conditions = " OR ".join("(unit_number=%s AND face=%s AND lane=%s)"
                             for _unit in units for _face in faces for _lane in lanes)
    params = tuple(value for unit in units for slot_face in faces for lane in lanes
                   for value in (unit, slot_face, lane))
    for table in ("rack_equipment_slots", "rack_reservation_slots"):
        conflict = await db.fetch_one(
            "SELECT unit_number FROM " + table + " WHERE rack_id=%s AND (" + conditions + ")",
            (rack_id, *params))
        if conflict:
            raise ValueError(f"Rack unit {conflict['unit_number']} is already occupied or reserved")
    reservation_id = await db.execute_returning_lastrowid(
        """INSERT INTO rack_reservations
           (company_id,rack_id,start_unit,unit_height,face,width_lanes,start_lane,depth_mode,label,owner,notes)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (company_id, rack_id, start_unit, unit_height, face, width_lanes, start_lane,
         depth_mode, label, owner, notes))
    try:
        for unit in units:
            for slot_face in faces:
                for lane in lanes:
                    await db.execute(
                        "INSERT INTO rack_reservation_slots (reservation_id,rack_id,unit_number,face,lane) VALUES (%s,%s,%s,%s,%s)",
                        (reservation_id, rack_id, unit, slot_face, lane))
    except Exception as exc:
        await db.execute("DELETE FROM rack_reservations WHERE id=%s", (reservation_id,))
        raise ValueError("One or more rack units are already reserved") from exc
    return reservation_id


async def delete_record(table: str, record_id: int, company_id: int) -> None:
    allowed = {"ip_networks", "ip_addresses", "racks", "rack_equipment", "rack_reservations"}
    if table not in allowed:
        raise ValueError("Invalid record type")
    await db.execute("DELETE FROM " + table + " WHERE id=%s AND company_id=%s", (record_id, company_id))
