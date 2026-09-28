"""Layout and insight calculations for the graphical rack workspace.

The rack page renders racks as physical elevations. Keeping the geometry,
status, and heatmap calculations here (rather than in nested template loops)
keeps the template declarative and lets the calculations be unit tested.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.services import rack_faceplates, rack_item_types

FACES = ("front", "rear")
LANES = (1, 2, 3)
LANE_LABELS = {1: "left", 2: "centre", 3: "right"}
VIEWS = ("graphical", "overview")
HEAT_LEVELS = 8
# Visual port positions drawn on port-capable items; the real port count is
# still shown in labels and the item list.
MAX_DRAWN_PORTS = 48
_WARNING_WORDS = (
    "alert", "decommission", "degraded", "down", "error", "expired", "fail",
    "fault", "inactive", "maintenance", "offline", "retired", "warn",
)


def normalise_view(value: str | None) -> str:
    """Return a supported workspace view, defaulting to the graphical one."""
    view = (value or "").strip().lower()
    return view if view in VIEWS else "graphical"


def select_rack(racks: Sequence[Mapping[str, Any]], requested: Any) -> Mapping[str, Any] | None:
    """Return the requested rack, or the first rack when the id is unknown."""
    try:
        requested_id = int(requested)
    except (TypeError, ValueError):
        requested_id = None
    for rack in racks:
        if requested_id is not None and int(rack["id"]) == requested_id:
            return rack
    return racks[0] if racks else None


def equipment_status(item: Mapping[str, Any]) -> str:
    """Map a linked asset status onto the LED state drawn on the chassis."""
    status = str(item.get("asset_status") or "").strip().lower()
    if status and any(word in status for word in _WARNING_WORDS):
        return "warning"
    return "active"


def _faces_for(item: Mapping[str, Any]) -> tuple[str, ...]:
    return FACES if item.get("depth_mode") == "full" else (str(item.get("face") or "front"),)


def _grid_row(rack: Mapping[str, Any], item: Mapping[str, Any]) -> int:
    """Return the 1-based CSS grid row where an item's top edge sits."""
    start, height = int(item["start_unit"]), int(item["unit_height"])
    if rack.get("numbering_direction") == "top-down":
        return start
    return int(rack["unit_count"]) - start - height + 2


def unit_rows(rack: Mapping[str, Any]) -> list[int]:
    """Unit numbers in on-screen order, top of the rack first."""
    count = int(rack["unit_count"])
    if rack.get("numbering_direction") == "top-down":
        return list(range(1, count + 1))
    return list(range(count, 0, -1))


def _covers(item: Mapping[str, Any], unit: int, lane: int) -> bool:
    start, lane_start = int(item["start_unit"]), int(item["start_lane"])
    return (start <= unit < start + int(item["unit_height"])
            and lane_start <= lane < lane_start + int(item["width_lanes"]))


def _drawn_ports(item: Mapping[str, Any], rear: bool = False) -> list[dict[str, Any]]:
    """Return the connections physically on the viewed side of an item."""
    item_type = rack_item_types.get(item.get("item_type"))
    if not item_type.has_ports:
        return []
    on_side = lambda connector: (connector in item_type.rear_connectors) == rear  # noqa: E731
    ports = list(item.get("ports") or [])
    if ports:
        drawn = [{"number": int(port["port_number"]), "connector": str(port.get("connector") or "data"),
                  "linked": (port.get("asset_id") is not None or bool(port.get("label"))
                             or port.get("source_port_id") is not None
                             or port.get("peer_port_id") is not None or bool(port.get("peer"))
                             or bool(port.get("fed_items")))}
                 for port in ports]
    else:
        drawn = [{"number": 0, "connector": connector, "linked": False}
                 for connector, count in item_type.connectors for _ in range(count)]
        for number, port in enumerate(drawn, start=1):
            port["number"] = number
    return [port for port in drawn if on_side(port["connector"])][:MAX_DRAWN_PORTS]


def _side_view(catalogue: rack_item_types.RackItemType, item: Mapping[str, Any],
               width: int, height: int, rear: bool) -> dict[str, Any]:
    """How one side of an item is drawn: its image, repeat height and ports."""
    view: dict[str, Any] = {
        "rear": rear,
        "image": rack_item_types.image_path(catalogue.key, width, rear),
        "faceplate": None,
        "image_units": catalogue.image_units,
        "ports": _drawn_ports(item, rear),
    }
    if rack_faceplates.side_connectors(catalogue.key, rear):
        # Draw this side from the item's actual connections at full height.
        connections = rack_faceplates.connections_from_ports(item.get("ports") or [], catalogue.key, rear)
        view["faceplate"] = rack_faceplates.data_uri(
            rack_faceplates.render(catalogue.key, width, height, rear, connections))
        view["image_units"] = height
    return view


def _block(rack: Mapping[str, Any], item: Mapping[str, Any], kind: str,
           face: str = "front") -> dict[str, Any]:
    start, height = int(item["start_unit"]), int(item["unit_height"])
    lane, width = int(item["start_lane"]), int(item["width_lanes"])
    # A full-depth item is seen from behind on the face it is not mounted on.
    rear = face != str(item.get("face") or "front")
    image = None
    faceplate = None
    alternate = None
    ports: list[dict[str, Any]] = []
    if kind == "reservation":
        name = item.get("label") or "Reserved space"
        item_type, type_label, active, image_units = "reserved", "Reserved", False, 1
        status = "reserved"
    else:
        catalogue = rack_item_types.get(item.get("item_type"))
        name = item.get("name") or item.get("asset_name") or catalogue.label
        item_type, type_label = catalogue.key, catalogue.label
        active, image_units = catalogue.active, catalogue.image_units
        status = equipment_status(item)
        view = _side_view(catalogue, item, width, height, rear)
        image, faceplate = view["image"], view["faceplate"]
        image_units, ports = view["image_units"], view["ports"]
        # Full-depth items already appear on both elevations and move with them
        # when the rack is swivelled. Half-depth items appear on one face only,
        # so swivelling shows their other side (e.g. a NAS's back panel).
        if item.get("depth_mode") != "full":
            alternate = _side_view(catalogue, item, width, height, not rear)
    return {
        "kind": kind,
        "id": item["id"],
        "name": name,
        "item_type": item_type,
        "type_label": type_label,
        "active": active,
        "image_units": image_units,
        "status": status,
        "start_unit": start,
        "end_unit": start + height - 1,
        "unit_height": height,
        "start_lane": lane,
        "end_lane": lane + width - 1,
        "width_lanes": width,
        "grid_row": _grid_row(rack, item),
        "ports": ports,
        "rear": rear,
        "image": image,
        "faceplate": faceplate,
        "alternate": alternate,
        "port_count": int(item.get("port_count") or 0),
        "power_draw_watts": item.get("power_draw_watts"),
    }


def build_faces(rack: Mapping[str, Any], equipment: Iterable[Mapping[str, Any]],
                reservations: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the drawable blocks and free slots for both rack faces."""
    rack_equipment = [item for item in equipment if item["rack_id"] == rack["id"]]
    rack_reservations = [item for item in reservations if item["rack_id"] == rack["id"]]
    rows = unit_rows(rack)
    faces = []
    for face in FACES:
        on_face = [(item, "equipment") for item in rack_equipment if face in _faces_for(item)]
        on_face += [(item, "reservation") for item in rack_reservations if face in _faces_for(item)]
        open_slots = [
            {"row": row, "unit": unit, "lane": lane, "lane_label": LANE_LABELS[lane]}
            for row, unit in enumerate(rows, start=1)
            for lane in LANES
            if not any(_covers(item, unit, lane) for item, _kind in on_face)
        ]
        faces.append({
            "face": face,
            "blocks": [_block(rack, item, kind, face) for item, kind in on_face],
            "open_slots": open_slots,
        })
    return faces


def build_heatmap(rack: Mapping[str, Any], equipment: Iterable[Mapping[str, Any]],
                  reservations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return a unit-by-slot heatmap of recorded power density or occupancy.

    Rows are the six physical slots (front and rear, left to right lanes);
    columns are rack units from 1 upwards. When any item in the rack has a
    recorded power draw, each slot is coloured by that item's draw spread
    evenly over the slots it occupies. Otherwise the heatmap falls back to
    occupancy so it never implies readings the portal does not hold.
    """
    rack_equipment = [item for item in equipment if item["rack_id"] == rack["id"]]
    rack_reservations = [item for item in reservations if item["rack_id"] == rack["id"]]
    unit_count = int(rack["unit_count"])
    mode = "power" if any(item.get("power_draw_watts") is not None for item in rack_equipment) else "occupancy"

    def density(item: Mapping[str, Any]) -> float | None:
        watts = item.get("power_draw_watts")
        if watts is None:
            return None
        slots = int(item["unit_height"]) * int(item["width_lanes"]) * len(_faces_for(item))
        return float(watts) / slots if slots else None

    densities = [value for value in (density(item) for item in rack_equipment) if value is not None]
    peak = max(densities, default=0.0)
    rows = []
    for face in FACES:
        for lane in LANES:
            cells = []
            for unit in range(1, unit_count + 1):
                item = next((candidate for candidate in rack_equipment
                             if face in _faces_for(candidate) and _covers(candidate, unit, lane)), None)
                reserved = item is None and any(
                    face in _faces_for(candidate) and _covers(candidate, unit, lane)
                    for candidate in rack_reservations)
                watts = density(item) if item else None
                if mode == "power":
                    level = round(watts * HEAT_LEVELS / peak) if watts and peak else 0
                    state = "power" if watts is not None else ("unmetered" if item else ("reserved" if reserved else "open"))
                else:
                    level = HEAT_LEVELS - 1 if item else (HEAT_LEVELS // 2 - 1 if reserved else 0)
                    state = "installed" if item else ("reserved" if reserved else "open")
                if state == "power":
                    label = f"{watts:.0f} W per slot"
                elif state == "unmetered":
                    label = "Installed, no power reading"
                else:
                    label = state.title()
                cells.append({"unit": unit, "level": max(0, min(level, HEAT_LEVELS)), "state": state,
                              "watts": round(watts, 1) if watts is not None else None,
                              "label": f"U{unit} {face} {LANE_LABELS[lane]}: {label}"})
            rows.append({"face": face, "lane": lane,
                         "label": f"{face[0].upper()}{LANE_LABELS[lane][0].upper()}", "cells": cells})
    step = max(1, round(unit_count / 9))
    ticks = sorted({1, *range(1 + step, unit_count, step), unit_count})
    return {"mode": mode, "rows": rows, "units": unit_count, "ticks": ticks,
            "peak_watts": round(peak, 1), "levels": HEAT_LEVELS}


def edit_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return the fields the add/edit dialog needs to reopen a rack item."""
    catalogue = rack_item_types.get(item.get("item_type"))
    counts = {connector: 0 for connector in catalogue.connector_keys}
    for port in item.get("ports") or []:
        connector = str(port.get("connector") or "data")
        counts[connector] = counts.get(connector, 0) + 1
    return {
        "id": item["id"],
        "name": item.get("name") or "",
        "item_type": catalogue.key,
        "asset_id": item.get("asset_id"),
        "power_draw_watts": item.get("power_draw_watts"),
        "notes": item.get("notes") or "",
        "start_unit": int(item["start_unit"]),
        "unit_height": int(item["unit_height"]),
        "face": item.get("face") or "front",
        "width_lanes": int(item["width_lanes"]),
        "start_lane": int(item["start_lane"]),
        "depth_mode": item.get("depth_mode") or "half",
        "port_counts": counts,
        "ports": [
            {"connector": str(port.get("connector") or "data"), "ordinal": int(port.get("ordinal") or 0),
             "asset_id": port.get("asset_id"), "label": port.get("label") or "",
             "source_port_id": port.get("source_port_id"), "id": port.get("id"),
             "peer_port_id": port.get("peer_port_id"), "fed_port_id": port.get("fed_port_id"),
             "network_peer": port.get("network_peer")}
            for port in item.get("ports") or []
        ],
    }


def port_catalog(equipment: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every rack item's linkable ports, for choosing the remote end of a link.

    Includes items in every rack of the company, with the asset each item is
    linked to, so choosing an asset can offer that device's ports.
    """
    catalog = []
    for item in equipment:
        ports = [
            {"id": port["id"], "connector": str(port.get("connector") or "data"),
             "label": port.get("display_label") or "", "peer_port_id": port.get("peer_port_id"),
             "source_port_id": port.get("source_port_id"), "network_peer": port.get("network_peer")}
            for port in item.get("ports") or []
            if str(port.get("connector") or "data") in {"data", "psu"} and port.get("id") is not None
        ]
        if not ports:
            continue
        catalog.append({
            "id": item["id"],
            "name": item.get("name") or item.get("asset_name") or rack_item_types.get(item.get("item_type")).label,
            "rack": item.get("rack_name") or "",
            "rack_id": item.get("rack_id"),
            "asset_id": item.get("asset_id"),
            "ports": ports,
        })
    return catalog


def link_target_groups(catalog: Sequence[Mapping[str, Any]], selected_rack_id: Any = None) -> list[dict[str, Any]]:
    """Group the port catalogue by rack for the port link device picker.

    The selected rack comes first so its neighbours are quickest to reach;
    the other racks follow by name so an uplink's far end is easy to find.
    """
    groups: dict[Any, dict[str, Any]] = {}
    for entry in catalog:
        rack_id = entry.get("rack_id")
        group = groups.setdefault(rack_id, {
            "rack_id": rack_id, "rack": entry.get("rack") or "Unknown rack",
            "current": selected_rack_id is not None and rack_id == selected_rack_id,
            "entries": []})
        group["entries"].append(entry)
    return sorted(groups.values(), key=lambda group: (not group["current"], str(group["rack"]).lower()))


def _link_end(item: Mapping[str, Any], port: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rack_id": item.get("rack_id"),
        "rack": item.get("rack_name") or "",
        "equipment_id": item["id"],
        "name": item.get("name") or item.get("asset_name") or rack_item_types.get(item.get("item_type")).label,
        "port": port.get("display_label") or "",
        "label": port.get("label") or "",
    }


def rack_links(equipment: Iterable[Mapping[str, Any]], rack_id: Any = None) -> list[dict[str, Any]]:
    """Network links whose two ends sit in different racks (uplinks, trunks).

    Each link is listed once. With ``rack_id`` only links touching that rack
    are returned, with ``near`` always the end in that rack; otherwise ``near``
    is the end in the rack whose name sorts first.
    """
    items = list(equipment)
    ends: dict[int, dict[str, Any]] = {}
    for item in items:
        for port in item.get("ports") or []:
            if str(port.get("connector") or "data") == "data" and port.get("id") is not None:
                ends[int(port["id"])] = {"item": item, "port": port}
    links = []
    seen: set[frozenset[int]] = set()
    for port_id, end in ends.items():
        peer_id = end["port"].get("peer_port_id")
        far = ends.get(int(peer_id)) if peer_id is not None else None
        if far is None or far["item"].get("rack_id") == end["item"].get("rack_id"):
            continue
        key = frozenset({port_id, int(peer_id)})
        if key in seen:
            continue
        seen.add(key)
        near_end, far_end = _link_end(end["item"], end["port"]), _link_end(far["item"], far["port"])
        if rack_id is not None:
            if far_end["rack_id"] == rack_id:
                near_end, far_end = far_end, near_end
            elif near_end["rack_id"] != rack_id:
                continue
        elif (far_end["rack"].lower(), far_end["name"].lower()) < (near_end["rack"].lower(), near_end["name"].lower()):
            near_end, far_end = far_end, near_end
        links.append({"near": near_end, "far": far_end})
    links.sort(key=lambda link: (link["near"]["rack"].lower(), link["near"]["name"].lower(),
                                 link["far"]["rack"].lower(), link["far"]["name"].lower()))
    return links


def estate_summary(racks: Sequence[Mapping[str, Any]], equipment: Sequence[Mapping[str, Any]],
                   reservations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return totals across every rack for the overview and sidebar."""
    total_units = sum(int(rack["unit_count"]) for rack in racks)
    return {
        "rack_count": len(racks),
        "total_units": total_units,
        "equipment_count": len(equipment),
        "reservation_count": len(reservations),
        "warning_count": sum(1 for item in equipment if equipment_status(item) == "warning"),
    }


def build_workspace(data: Mapping[str, Any], requested_rack: Any, requested_view: str | None) -> dict[str, Any]:
    """Assemble the template context for the rack workspace."""
    racks = list(data.get("racks") or [])
    equipment = list(data.get("equipment") or [])
    reservations = list(data.get("reservations") or [])
    selected = select_rack(racks, requested_rack)
    workspace: dict[str, Any] = {
        "view": normalise_view(requested_view),
        "selected_rack": selected,
        "estate": estate_summary(racks, equipment, reservations),
        "faces": [],
        "heatmap": None,
        "reservation_count": 0,
        "warning_count": 0,
        "items": [],
        "rack_links": [],
        "link_targets": [],
        "rack_link_counts": {},
    }
    all_links = rack_links(equipment)
    workspace["estate"]["rack_link_count"] = len(all_links)
    for link in all_links:
        for end in (link["near"], link["far"]):
            workspace["rack_link_counts"][end["rack_id"]] = workspace["rack_link_counts"].get(end["rack_id"], 0) + 1
    if workspace["view"] == "overview":
        workspace["rack_links"] = all_links
    if selected is not None:
        workspace["link_targets"] = link_target_groups(port_catalog(equipment), selected["id"])
        workspace["faces"] = build_faces(selected, equipment, reservations)
        workspace["heatmap"] = build_heatmap(selected, equipment, reservations)
        workspace["reservation_count"] = sum(1 for item in reservations if item["rack_id"] == selected["id"])
        workspace["warning_count"] = sum(
            1 for item in equipment
            if item["rack_id"] == selected["id"] and equipment_status(item) == "warning")
        workspace["items"] = [edit_payload(item) for item in equipment if item["rack_id"] == selected["id"]]
        if workspace["view"] != "overview":
            workspace["rack_links"] = rack_links(equipment, selected["id"])
    return workspace
