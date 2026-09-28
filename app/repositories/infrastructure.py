"""Company-scoped IP address management and rack documentation."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.core.database import db
from app.services import rack_item_types

MAX_RACK_PORTS = 1000
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


def _number_ports(ports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give each port its per-connector ordinal and display name ("IEC 2")."""
    ordinals: dict[str, int] = {}
    numbered = []
    for port in sorted(ports, key=lambda row: int(row["port_number"])):
        connector = str(port.get("connector") or "data")
        ordinals[connector] = ordinals.get(connector, 0) + 1
        numbered.append({**port, "connector": connector, "ordinal": ordinals[connector],
                         "display_label": rack_item_types.connector_label(connector, ordinals[connector])})
    return numbered


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
    equipment_by_id = {item["id"]: item for item in equipment}
    ports_by_id: dict[int, dict[str, Any]] = {}
    for item in equipment:
        item["ports"] = _number_ports(ports_by_equipment.get(item["id"], []))
        for port in item["ports"]:
            port["equipment_name"] = item.get("name") or item.get("asset_name") or "Rack item"
            port["rack_name"] = item.get("rack_name")
            port["rack_id"] = item.get("rack_id")
            port["fed_items"] = []
            port["fed_port_id"] = None
            ports_by_id[port["id"]] = port
    for item in equipment:
        for port in item["ports"]:
            # Links to ports that no longer exist (a removed item) are ignored.
            source = ports_by_id.get(port.get("source_port_id"))
            peer = ports_by_id.get(port.get("peer_port_id"))
            port["source_port_id"] = source["id"] if source else None
            port["peer_port_id"] = peer["id"] if peer else None
            port["source"] = f"{source['equipment_name']} · {source['display_label']}" if source else None
            port["peer"] = f"{peer['equipment_name']} · {peer['display_label']}" if peer else None
            # Uplinks to another rack name the far rack so the link reads on its own.
            port["peer_equipment_id"] = peer["equipment_id"] if peer else None
            port["peer_rack_id"] = peer["rack_id"] if peer else None
            port["peer_rack_name"] = peer["rack_name"] if peer else None
            if peer and peer["rack_id"] != item.get("rack_id"):
                port["peer"] += f" ({peer['rack_name']})"
            if source:
                source["fed_items"].append(f"{port['equipment_name']} · {port['display_label']}")
                source["fed_port_id"] = port["id"]
    for item in equipment:
        item["linked_port_count"] = sum(
            1 for port in item["ports"]
            if port.get("asset_id") or port.get("label") or port["fed_items"] or port["source"] or port["peer"])
    power_outlets = [
        {"id": port["id"], "equipment_id": port["equipment_id"],
         "label": f"{port['rack_name']} · {port['equipment_name']} · {port['display_label']}"}
        for item in equipment for port in item["ports"]
        if port.get("connector") in rack_item_types.OUTLET_CONNECTORS and item["id"] in equipment_by_id]
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
            "equipment": equipment, "reservations": reservations,
            "power_outlets": power_outlets}


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


class RackResizeBlocked(ValueError):
    """Shrinking a rack would drop units that still hold items or reservations."""


async def resize_rack(company_id: int, rack_id: int, unit_count: int, depth_mm: int) -> dict[str, Any]:
    """Change a rack's height and depth, returning its previous dimensions.

    Growing is always allowed. Shrinking removes the highest-numbered units,
    so it is refused while any item or reservation still sits in them; the
    technician has to move or remove those first.
    """
    if not 1 <= unit_count <= 100:
        raise ValueError("Rack size must be between 1 and 100 units")
    if not 100 <= depth_mm <= 5000:
        raise ValueError("Rack depth must be between 100 and 5000 mm")
    rack = await db.fetch_one(
        "SELECT unit_count, depth_mm FROM racks WHERE id=%s AND company_id=%s", (rack_id, company_id))
    if not rack:
        raise ValueError("Rack not found")
    if unit_count < int(rack["unit_count"]):
        blocking = [
            f"{row['label'] or fallback} (U{row['start_unit']}–{row['end_unit']})"
            for sql, fallback in (
                ("""SELECT COALESCE(e.name, a.name) label, e.start_unit,
                           e.start_unit + e.unit_height - 1 end_unit
                    FROM rack_equipment e LEFT JOIN assets a ON a.id=e.asset_id
                    WHERE e.rack_id=%s AND e.company_id=%s AND e.start_unit + e.unit_height - 1 > %s
                    ORDER BY e.start_unit""", "Rack item"),
                ("""SELECT label, start_unit, start_unit + unit_height - 1 end_unit
                    FROM rack_reservations
                    WHERE rack_id=%s AND company_id=%s AND start_unit + unit_height - 1 > %s
                    ORDER BY start_unit""", "Reserved space"))
            for row in (await db.fetch_all(sql, (rack_id, company_id, unit_count)) or [])]
        if blocking:
            raise RackResizeBlocked(
                f"Move or remove everything above U{unit_count} before shrinking this rack: "
                + ", ".join(blocking))
    await db.execute("UPDATE racks SET unit_count=%s,depth_mm=%s WHERE id=%s AND company_id=%s",
                     (unit_count, depth_mm, rack_id, company_id))
    return {"unit_count": rack["unit_count"], "depth_mm": rack["depth_mm"]}


def _item_name(name: str | None, asset: dict[str, Any] | None, item_type: str) -> str:
    """Return the display name for a rack item, falling back to its asset or type."""
    clean = (name or "").strip()
    if clean:
        return clean
    asset_name = str((asset or {}).get("name") or "").strip()
    return asset_name[:191] or rack_item_types.get(item_type).label


@dataclass(frozen=True)
class PortLink:
    """What is connected to one port.

    Data, outlet and KVM device ports link to a company asset and/or a
    free-text label. A power supply (PSU) instead records the outlet on
    another unit that feeds it, and/or a label such as a wall circuit.

    ``peer_port_id`` links to a specific port on another rack item: a data
    port to the remote network port (stored on both ends), or an outlet to
    the PSU it feeds (stored as that PSU's source).
    """
    connector: str
    ordinal: int
    asset_id: int | None = None
    label: str | None = None
    source_port_id: int | None = None
    peer_port_id: int | None = None


def _check_position(face: str, depth_mode: str, unit_height: int, width_lanes: int,
                    start_lane: int) -> None:
    if (face not in {"front", "rear"} or depth_mode not in {"half", "full"}
            or unit_height < 1 or width_lanes not in {1, 2, 3}
            or start_lane < 1 or start_lane + width_lanes - 1 > 3):
        raise ValueError("Invalid rack item or position")


def _port_counts(item_type: str, port_counts: Mapping[str, int] | None,
                 port_count: int = 0) -> dict[str, int]:
    """Return connector counts for the connectors the item type documents."""
    catalogue = rack_item_types.get(item_type)
    if port_counts is None:
        keys = catalogue.connector_keys
        port_counts = {keys[0]: port_count} if keys else {}
    counts = {key: int(port_counts.get(key) or 0) for key in catalogue.connector_keys}
    if any(count < 0 for count in counts.values()) or sum(counts.values()) > MAX_RACK_PORTS:
        raise ValueError(f"A rack item can document up to {MAX_RACK_PORTS} ports")
    return counts


async def _company_asset(company_id: int, asset_id: int | None) -> dict[str, Any] | None:
    if asset_id is None:
        return None
    asset = await db.fetch_one(
        "SELECT id, name FROM assets WHERE id=%s AND company_id=%s", (asset_id, company_id))
    if not asset:
        raise ValueError("Asset does not belong to this company")
    return asset


async def _slot_conflict(rack_id: int, units: list[int], faces: list[str], lanes: list[int],
                         exclude_equipment_id: int | None = None) -> int | None:
    conditions = " OR ".join("(unit_number=%s AND face=%s AND lane=%s)"
                             for _unit in units for _face in faces for _lane in lanes)
    params = tuple(value for unit in units for slot_face in faces for lane in lanes
                   for value in (unit, slot_face, lane))
    for table in ("rack_equipment_slots", "rack_reservation_slots"):
        sql = "SELECT unit_number FROM " + table + " WHERE rack_id=%s AND (" + conditions + ")"
        args: tuple[Any, ...] = (rack_id, *params)
        if table == "rack_equipment_slots" and exclude_equipment_id is not None:
            sql += " AND equipment_id<>%s"
            args = (*args, exclude_equipment_id)
        conflict = await db.fetch_one(sql, args)
        if conflict:
            return int(conflict["unit_number"])
    return None


async def _insert_slots(equipment_id: int, rack_id: int, units: list[int], faces: list[str],
                        lanes: list[int]) -> None:
    for unit in units:
        for slot_face in faces:
            for lane in lanes:
                await db.execute(
                    "INSERT INTO rack_equipment_slots (equipment_id,rack_id,unit_number,face,lane) VALUES (%s,%s,%s,%s,%s)",
                    (equipment_id, rack_id, unit, slot_face, lane))


async def _apply_port_links(company_id: int, equipment_id: int,
                            ports: list[dict[str, Any]], links: Iterable[PortLink]) -> None:
    """Record what is connected to each port, matching links by connector and ordinal."""
    by_position: dict[tuple[str, int], dict[str, Any]] = {}
    ordinals: dict[str, int] = {}
    for port in sorted(ports, key=lambda row: int(row["port_number"])):
        connector = str(port.get("connector") or "data")
        ordinals[connector] = ordinals.get(connector, 0) + 1
        by_position[(connector, ordinals[connector])] = port
    links = [link for link in links if (link.connector, link.ordinal) in by_position]
    power = rack_item_types.POWER_INPUT
    asset_ids = sorted({link.asset_id for link in links
                        if link.asset_id is not None and link.connector != power
                        and link.peer_port_id is None})
    if asset_ids:
        found = await db.fetch_all(
            "SELECT id FROM assets WHERE company_id=%s AND id IN (" + ",".join(["%s"] * len(asset_ids)) + ")",
            (company_id, *asset_ids)) or []
        if {int(row["id"]) for row in found} != set(asset_ids):
            raise ValueError("Asset does not belong to this company")
    source_ids = sorted({link.source_port_id for link in links
                         if link.source_port_id is not None and link.connector == power})
    if source_ids:
        outlets = await db.fetch_all(
            "SELECT id, equipment_id FROM rack_equipment_ports WHERE company_id=%s AND connector IN ('iec','3pin') AND id IN ("
            + ",".join(["%s"] * len(source_ids)) + ")",
            (company_id, *source_ids)) or []
        valid = {int(row["id"]) for row in outlets if int(row["equipment_id"]) != equipment_id}
        if valid != set(source_ids):
            raise ValueError("A power supply must be fed from an outlet on another UPS or PDU")
    peer_links = [link for link in links if link.connector in PEER_TARGETS]
    await _check_peer_targets(company_id, equipment_id, peer_links)
    port_ids: dict[int, int] = {}
    if peer_links:
        rows = await db.fetch_all(
            "SELECT id, port_number FROM rack_equipment_ports WHERE equipment_id=%s AND company_id=%s",
            (equipment_id, company_id)) or []
        port_ids = {int(row["port_number"]): int(row["id"]) for row in rows}
    for link in links:
        label = (link.label or "").strip()[:191] or None
        port = by_position[(link.connector, link.ordinal)]
        is_power = link.connector == power
        # A link to a specific remote port replaces a bare asset link.
        asset_id = None if is_power or link.peer_port_id is not None else link.asset_id
        await db.execute(
            "UPDATE rack_equipment_ports SET asset_id=%s,label=%s,source_port_id=%s WHERE equipment_id=%s AND port_number=%s AND company_id=%s",
            (asset_id, label, link.source_port_id if is_power else None,
             equipment_id, int(port["port_number"]), company_id))
        port_id = port_ids.get(int(port["port_number"]))
        if link.connector in PEER_TARGETS and port_id is not None:
            await _set_peer(company_id, link.connector, port_id, link.peer_port_id)


# Which remote connector each connector can be linked to port-to-port.
PEER_TARGETS = {"data": "data", "iec": "psu", "3pin": "psu"}


async def _check_peer_targets(company_id: int, equipment_id: int, links: list[PortLink]) -> None:
    targets = {link.peer_port_id: PEER_TARGETS[link.connector] for link in links if link.peer_port_id is not None}
    if not targets:
        return
    rows = await db.fetch_all(
        "SELECT id, equipment_id, connector FROM rack_equipment_ports WHERE company_id=%s AND id IN ("
        + ",".join(["%s"] * len(targets)) + ")", (company_id, *targets)) or []
    found = {int(row["id"]): row for row in rows}
    for target_id, connector in targets.items():
        row = found.get(target_id)
        if (not row or str(row.get("connector") or "data") != connector
                or int(row["equipment_id"]) == equipment_id):
            noun = "power supply" if connector == "psu" else "network port"
            raise ValueError(f"Choose a {noun} on another rack item")


async def _set_peer(company_id: int, connector: str, port_id: int, target_id: int | None) -> None:
    """Link one port to a remote port, replacing any earlier link on either end."""
    if connector == "data":
        # Network links are stored on both ends.
        for end in {port_id, target_id} - {None}:
            await db.execute(
                "UPDATE rack_equipment_ports SET peer_port_id=NULL WHERE company_id=%s AND (peer_port_id=%s OR id=%s)",
                (company_id, end, end))
        if target_id is not None:
            await db.execute("UPDATE rack_equipment_ports SET peer_port_id=%s WHERE company_id=%s AND id=%s",
                             (target_id, company_id, port_id))
            await db.execute("UPDATE rack_equipment_ports SET peer_port_id=%s WHERE company_id=%s AND id=%s",
                             (port_id, company_id, target_id))
        return
    # An outlet feeds at most one PSU; the feed is recorded on the PSU.
    await db.execute("UPDATE rack_equipment_ports SET source_port_id=NULL WHERE company_id=%s AND source_port_id=%s",
                     (company_id, port_id))
    if target_id is not None:
        await db.execute("UPDATE rack_equipment_ports SET source_port_id=%s WHERE company_id=%s AND id=%s",
                         (port_id, company_id, target_id))


async def _sync_ports(company_id: int, equipment_id: int, counts: Mapping[str, int]) -> list[dict[str, Any]]:
    """Add or remove ports so each connector has the requested count.

    Existing ports and their links are kept; removals take the highest
    numbered ports of that connector first.
    """
    rows = list(await db.fetch_all(
        "SELECT id, port_number, connector FROM rack_equipment_ports WHERE equipment_id=%s AND company_id=%s ORDER BY port_number",
        (equipment_id, company_id)) or [])
    removed: list[int] = []
    kept: list[dict[str, Any]] = []
    for connector in {str(row.get("connector") or "data") for row in rows} | set(counts):
        existing = [row for row in rows if str(row.get("connector") or "data") == connector]
        keep = counts.get(connector, 0)
        kept += existing[:keep]
        removed += [int(row["id"]) for row in existing[keep:]]
    if removed:
        marks = ",".join(["%s"] * len(removed))
        await db.execute("UPDATE rack_equipment_ports SET source_port_id=NULL WHERE source_port_id IN (" + marks + ")", tuple(removed))
        await db.execute("UPDATE rack_equipment_ports SET peer_port_id=NULL WHERE peer_port_id IN (" + marks + ")", tuple(removed))
        await db.execute("DELETE FROM rack_equipment_ports WHERE id IN (" + marks + ")", tuple(removed))
    next_number = max((int(row["port_number"]) for row in rows), default=0) + 1
    for connector, count in counts.items():
        have = sum(1 for row in kept if str(row.get("connector") or "data") == connector)
        for _ in range(count - have):
            await db.execute(
                "INSERT INTO rack_equipment_ports (company_id,equipment_id,port_number,connector) VALUES (%s,%s,%s,%s)",
                (company_id, equipment_id, next_number, connector))
            kept.append({"port_number": next_number, "connector": connector})
            next_number += 1
    return kept


async def place_asset(company_id: int, rack_id: int, asset_id: int | None, start_unit: int,
                      unit_height: int, face: str, notes: str | None,
                      width_lanes: int = 3, start_lane: int = 1,
                      depth_mode: str = "half", power_draw_watts: int | None = None,
                      item_type: str = "server", name: str | None = None,
                      port_count: int = 0, *, port_counts: Mapping[str, int] | None = None,
                      port_links: Iterable[PortLink] = ()) -> int:
    item_type = rack_item_types.normalise(item_type)
    _check_position(face, depth_mode, unit_height, width_lanes, start_lane)
    if power_draw_watts is not None and power_draw_watts < 0:
        raise ValueError("Invalid rack item or position")
    counts = _port_counts(item_type, port_counts, port_count)
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
    conflict = await _slot_conflict(rack_id, units, faces, lanes)
    if conflict is not None:
        raise ValueError(f"Rack unit {conflict} is already occupied or reserved")
    columns = ["company_id", "rack_id", "asset_id", "start_unit", "unit_height", "face", "notes",
               "width_lanes", "start_lane", "depth_mode", "power_draw_watts", "item_type", "name", "port_count"]
    values: list[Any] = [company_id, rack_id, asset_id, start_unit, unit_height, face, notes, width_lanes,
                         start_lane, depth_mode, power_draw_watts, item_type, clean_name, sum(counts.values())]
    equipment_id = await db.execute_returning_lastrowid(
        "INSERT INTO rack_equipment (" + ",".join(columns) + ") VALUES (" + ",".join(["%s"] * len(columns)) + ")",
        tuple(values))
    try:
        await _insert_slots(equipment_id, rack_id, units, faces, lanes)
        ports: list[dict[str, Any]] = []
        number = 1
        for connector, count in counts.items():
            for _ in range(count):
                await db.execute(
                    "INSERT INTO rack_equipment_ports (company_id,equipment_id,port_number,connector) VALUES (%s,%s,%s,%s)",
                    (company_id, equipment_id, number, connector))
                ports.append({"port_number": number, "connector": connector})
                number += 1
    except Exception as exc:
        await db.execute("DELETE FROM rack_equipment WHERE id=%s", (equipment_id,))
        raise ValueError("One or more rack units are already occupied") from exc
    links = list(port_links)
    if links:
        try:
            await _apply_port_links(company_id, equipment_id, ports, links)
        except ValueError:
            await db.execute("DELETE FROM rack_equipment WHERE id=%s", (equipment_id,))
            raise
    return equipment_id


async def link_equipment_port(company_id: int, equipment_id: int, port_number: int,
                              asset_id: int | None, label: str | None = None) -> None:
    port = await db.fetch_one(
        "SELECT id FROM rack_equipment_ports WHERE company_id=%s AND equipment_id=%s AND port_number=%s",
        (company_id, equipment_id, port_number))
    if not port:
        raise ValueError("Rack item port not found")
    if asset_id is not None and not await db.fetch_one(
            "SELECT id FROM assets WHERE id=%s AND company_id=%s", (asset_id, company_id)):
        raise ValueError("Asset does not belong to this company")
    clean_label = (label or "").strip()[:191] or None
    await db.execute("UPDATE rack_equipment_ports SET asset_id=%s,label=%s WHERE id=%s AND company_id=%s",
                     (asset_id, clean_label, port["id"], company_id))


@dataclass(frozen=True)
class RackPosition:
    start_unit: int
    unit_height: int
    face: str
    width_lanes: int
    start_lane: int
    depth_mode: str


async def update_rack_equipment(company_id: int, equipment_id: int, name: str | None,
                                item_type: str, asset_id: int | None,
                                power_draw_watts: int | None, notes: str | None, *,
                                position: RackPosition | None = None,
                                port_counts: Mapping[str, int] | None = None,
                                port_links: Iterable[PortLink] | None = None) -> None:
    """Update a rack item, optionally moving it and changing its connections.

    Callers that omit ``position``, ``port_counts`` and ``port_links`` leave the
    placement and existing ports untouched.
    """
    item_type = rack_item_types.normalise(item_type)
    if len((name or "").strip()) > 191:
        raise ValueError("Rack item name is too long")
    if power_draw_watts is not None and power_draw_watts < 0:
        raise ValueError("Power draw cannot be negative")
    item = await db.fetch_one(
        "SELECT * FROM rack_equipment WHERE id=%s AND company_id=%s", (equipment_id, company_id))
    if not item:
        raise ValueError("Rack item not found")
    asset = await _company_asset(company_id, asset_id)
    clean_name = _item_name(name, asset, item_type)
    catalogue = rack_item_types.get(item_type)
    counts = _port_counts(item_type, port_counts) if port_counts is not None else (
        None if catalogue.has_ports else {})
    assignments = ["name=%s", "item_type=%s", "asset_id=%s", "power_draw_watts=%s", "notes=%s"]
    values: list[Any] = [clean_name, item_type, asset_id, power_draw_watts, notes]
    old_slots: list[dict[str, Any]] = []
    if position is not None:
        _check_position(position.face, position.depth_mode, position.unit_height,
                        position.width_lanes, position.start_lane)
        rack = await db.fetch_one("SELECT unit_count FROM racks WHERE id=%s AND company_id=%s",
                                  (item["rack_id"], company_id))
        if (not rack or position.start_unit < 1
                or position.start_unit + position.unit_height - 1 > int(rack["unit_count"])):
            raise ValueError("Rack position is outside the rack")
        units = list(range(position.start_unit, position.start_unit + position.unit_height))
        lanes = list(range(position.start_lane, position.start_lane + position.width_lanes))
        faces = ["front", "rear"] if position.depth_mode == "full" else [position.face]
        conflict = await _slot_conflict(int(item["rack_id"]), units, faces, lanes, equipment_id)
        if conflict is not None:
            raise ValueError(f"Rack unit {conflict} is already occupied or reserved")
        old_slots = list(await db.fetch_all(
            "SELECT unit_number, face, lane FROM rack_equipment_slots WHERE equipment_id=%s",
            (equipment_id,)) or [])
        await db.execute("DELETE FROM rack_equipment_slots WHERE equipment_id=%s", (equipment_id,))
        try:
            await _insert_slots(equipment_id, int(item["rack_id"]), units, faces, lanes)
        except Exception as exc:
            await db.execute("DELETE FROM rack_equipment_slots WHERE equipment_id=%s", (equipment_id,))
            for slot in old_slots:
                await db.execute(
                    "INSERT INTO rack_equipment_slots (equipment_id,rack_id,unit_number,face,lane) VALUES (%s,%s,%s,%s,%s)",
                    (equipment_id, item["rack_id"], slot["unit_number"], slot["face"], slot["lane"]))
            raise ValueError("One or more rack units are already occupied") from exc
        assignments += ["start_unit=%s", "unit_height=%s", "face=%s", "width_lanes=%s",
                        "start_lane=%s", "depth_mode=%s"]
        values += [position.start_unit, position.unit_height, position.face,
                   position.width_lanes, position.start_lane, position.depth_mode]
    if counts is not None:
        assignments.append("port_count=%s")
        values.append(sum(counts.values()))
    await db.execute(
        "UPDATE rack_equipment SET " + ",".join(assignments) + " WHERE id=%s AND company_id=%s",
        (*values, equipment_id, company_id))
    if counts is not None:
        ports = await _sync_ports(company_id, equipment_id, counts)
        if port_links is not None:
            await _apply_port_links(company_id, equipment_id, ports, port_links)


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
