"""Network links between racks (uplinks) are listed once and name the far rack."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from app.repositories import infrastructure
from app.services import rack_dashboard

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "app/templates/infrastructure/racks.html").read_text()
SCRIPT = (ROOT / "app/static/js/racks.js").read_text()


def _item(item_id, rack_id, rack_name, name, ports, item_type="switch"):
    return {"id": item_id, "rack_id": rack_id, "rack_name": rack_name, "name": name,
            "item_type": item_type, "asset_id": None, "ports": ports}


def _port(port_id, label, peer=None, text=""):
    return {"id": port_id, "port_number": port_id % 100, "connector": "data", "display_label": label,
            "peer_port_id": peer, "label": text}


def _estate():
    return [
        _item(1, 10, "Comms A", "Core switch", [_port(101, "Port 47", 201, "10G fibre"),
                                                  _port(102, "Port 48", 301),
                                                  _port(103, "Port 1", 104)]),
        # A link inside one rack is not an uplink between racks.
        _item(5, 10, "Comms A", "Patch panel", [_port(104, "Port 1", 103)], "patch_panel"),
        _item(2, 20, "Comms B", "Access switch", [_port(201, "Port 1", 101)]),
        _item(3, 30, "Branch", "Edge router", [_port(301, "Port 2", 102, "WAN uplink")]),
    ]


def test_rack_links_lists_each_cross_rack_link_once():
    links = rack_dashboard.rack_links(_estate())

    assert [(link["near"]["name"], link["near"]["port"], link["far"]["rack"], link["far"]["port"])
            for link in links] == [
        ("Edge router", "Port 2", "Comms A", "Port 48"),
        ("Core switch", "Port 47", "Comms B", "Port 1"),
    ]
    assert links[1]["near"]["label"] == "10G fibre"


def test_rack_links_for_a_rack_put_that_rack_on_the_near_side():
    links = rack_dashboard.rack_links(_estate(), 20)

    assert len(links) == 1
    assert links[0]["near"]["name"] == "Access switch"
    assert (links[0]["far"]["rack_id"], links[0]["far"]["equipment_id"]) == (10, 1)


def test_workspace_carries_rack_links_counts_and_grouped_link_targets():
    racks = [{"id": 10, "name": "Comms A", "unit_count": 42},
             {"id": 20, "name": "Comms B", "unit_count": 42},
             {"id": 30, "name": "Branch", "unit_count": 12}]
    equipment = [dict(item, start_unit=1, unit_height=1, start_lane=1, width_lanes=3,
                      face="front", depth_mode="half") for item in _estate()]
    data = {"racks": racks, "equipment": equipment, "reservations": []}

    rack_view = rack_dashboard.build_workspace(data, "20", None)
    assert [link["far"]["name"] for link in rack_view["rack_links"]] == ["Core switch"]
    # The selected rack's items come first, then the other racks by name.
    assert [(group["rack"], group["current"]) for group in rack_view["link_targets"]] == [
        ("Comms B", True), ("Branch", False), ("Comms A", False)]
    assert rack_view["rack_link_counts"] == {10: 2, 20: 1, 30: 1}

    overview = rack_dashboard.build_workspace(data, None, "overview")
    assert len(overview["rack_links"]) == 2
    assert overview["estate"]["rack_link_count"] == 2


def test_overview_names_the_far_rack_on_cross_rack_peers(monkeypatch):
    racks = [{"id": 10, "name": "Comms A", "unit_count": 42}, {"id": 20, "name": "Comms B", "unit_count": 42}]
    equipment = [
        {"id": 1, "rack_id": 10, "rack_name": "Comms A", "name": "Core switch", "item_type": "switch",
         "unit_height": 1, "width_lanes": 3, "depth_mode": "half", "power_draw_watts": None},
        {"id": 2, "rack_id": 20, "rack_name": "Comms B", "name": "Access switch", "item_type": "switch",
         "unit_height": 1, "width_lanes": 3, "depth_mode": "half", "power_draw_watts": None},
    ]
    ports = [
        {"id": 101, "equipment_id": 1, "port_number": 1, "connector": "data", "peer_port_id": 201},
        {"id": 201, "equipment_id": 2, "port_number": 1, "connector": "data", "peer_port_id": 101},
    ]
    monkeypatch.setattr(infrastructure.db, "fetch_all",
                        AsyncMock(side_effect=[[], [], racks, equipment, ports, []]))

    result = asyncio.run(infrastructure.overview(1))

    core_port = result["equipment"][0]["ports"][0]
    assert core_port["peer"] == "Access switch · Port 1 (Comms B)"
    assert (core_port["peer_rack_id"], core_port["peer_equipment_id"]) == (20, 2)


def test_rack_page_shows_uplinks_and_picker_groups_items_by_rack():
    assert "Links to other racks" in TEMPLATE
    assert "Inter-rack links" in TEMPLATE
    assert "{% for group in workspace.link_targets %}" in TEMPLATE
    assert "Rack items without an asset" not in TEMPLATE
    assert "const deviceValueFor = (entry) => `item:${entry.id}`;" in SCRIPT
    assert "querySelectorAll('optgroup[data-rack-items]')" in SCRIPT
