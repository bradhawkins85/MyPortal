"""Layout and insight calculations for the graphical rack workspace.

The rack page renders racks as physical elevations. Keeping the geometry,
status, and heatmap calculations here (rather than in nested template loops)
keeps the template declarative and lets the calculations be unit tested.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.services import rack_item_types

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


def _drawn_ports(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    item_type = rack_item_types.get(item.get("item_type"))
    if not item_type.has_ports:
        return []
    ports = list(item.get("ports") or [])
    if ports:
        return [{"number": int(port["port_number"]), "linked": port.get("asset_id") is not None}
                for port in ports[:MAX_DRAWN_PORTS]]
    count = int(item.get("port_count") or 0) or item_type.default_ports
    return [{"number": number, "linked": False} for number in range(1, min(count, MAX_DRAWN_PORTS) + 1)]


def _block(rack: Mapping[str, Any], item: Mapping[str, Any], kind: str) -> dict[str, Any]:
    start, height = int(item["start_unit"]), int(item["unit_height"])
    lane, width = int(item["start_lane"]), int(item["width_lanes"])
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
        "ports": _drawn_ports(item) if kind == "equipment" else [],
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
            "blocks": [_block(rack, item, kind) for item, kind in on_face],
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
    }
    if selected is not None:
        workspace["faces"] = build_faces(selected, equipment, reservations)
        workspace["heatmap"] = build_heatmap(selected, equipment, reservations)
        workspace["reservation_count"] = sum(1 for item in reservations if item["rack_id"] == selected["id"])
        workspace["warning_count"] = sum(
            1 for item in equipment
            if item["rack_id"] == selected["id"] and equipment_status(item) == "warning")
    return workspace
