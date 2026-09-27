"""Rack faceplates drawn from an item's actual connections."""
from urllib.parse import unquote

from app.services import rack_dashboard, rack_faceplates as F


def _flags(count, linked):
    return [index < linked for index in range(count)]


def _psus(svg):
    return svg.count('fill="#1b2027" rx="2.0" stroke="#6c7686"')


def test_server_rear_draws_every_port_and_marks_connected_ones():
    svg = F.render("server", 3, 1, True, {"data": _flags(8, 4), "psu": _flags(1, 1)})
    assert svg.count(f'fill="{F.CABLE_DATA}"') == 4  # plugged cables
    assert svg.count(f'fill="{F.LED_GREEN}"') >= 5  # 4 port LEDs + the fed PSU
    assert _psus(svg) == 1


def test_psu_count_is_per_item_not_per_rack_unit():
    one = F.render("server", 3, 2, True, {"data": [], "psu": _flags(1, 0)})
    two = F.render("server", 3, 2, True, {"data": [], "psu": _flags(2, 1)})
    assert _psus(one) == 1 and _psus(two) == 2
    assert 'viewBox="0 0 480 88"' in two  # drawn at the item's full 2U height


def test_pdu_front_shows_its_actual_iec_and_three_pin_outlets():
    svg = F.render("pdu", 3, 1, False, {"iec": _flags(6, 2), "3pin": _flags(4, 1)})
    assert svg.count('fill="#050608" stroke="#a4acb8"') == 6  # IEC outlet bodies
    assert svg.count('fill="#e5e7eb" rx="3.0" stroke="#9aa3ae"') == 4  # 3-pin sockets


def test_connections_are_grouped_by_side_from_ports():
    ports = [
        {"port_number": 1, "connector": "data", "asset_id": 5},
        {"port_number": 2, "connector": "data"},
        {"port_number": 3, "connector": "psu", "source_port_id": 41},
    ]
    assert F.connections_from_ports(ports, "server", True) == {"data": [True, False], "psu": [True]}
    assert F.connections_from_ports(ports, "server", False) == {}
    assert F.side_connectors("switch", False) == ("data",) and F.side_connectors("switch", True) == ("psu",)


def test_blocks_use_a_dynamic_faceplate_only_on_sides_with_connections():
    rack = {"id": 1, "name": "Core", "unit_count": 4, "numbering_direction": "bottom-up"}
    server = {
        "id": 3, "rack_id": 1, "name": "Web", "item_type": "server", "start_unit": 1, "unit_height": 2,
        "start_lane": 1, "width_lanes": 3, "face": "front", "depth_mode": "full",
        "ports": [{"port_number": 1, "connector": "psu", "source_port_id": 9}],
    }
    front, rear = rack_dashboard.build_faces(rack, [server], [])
    assert front["blocks"][0]["faceplate"] is None
    block = rear["blocks"][0]
    assert block["faceplate"].startswith("data:image/svg+xml,") and block["image_units"] == 2
    svg = unquote(block["faceplate"].split(",", 1)[1])
    assert _psus(svg) == 1 and "'" not in block["faceplate"]


def test_static_images_are_generated_from_the_same_drawers():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    static = (root / "app/static/images/racks/pdu-w3.svg").read_text().strip()
    assert static == F.render("pdu", 3, 1, False)


def test_swivel_view_shows_the_back_of_half_depth_items():
    rack = {"id": 1, "name": "Core", "unit_count": 4, "numbering_direction": "bottom-up"}
    nas = {
        "id": 5, "rack_id": 1, "name": "NAS", "item_type": "storage", "start_unit": 1, "unit_height": 2,
        "start_lane": 1, "width_lanes": 3, "face": "front", "depth_mode": "half",
        "ports": [{"port_number": 1, "connector": "data", "asset_id": 2},
                  {"port_number": 2, "connector": "psu", "source_port_id": 9}],
    }
    pdu = {
        "id": 6, "rack_id": 1, "name": "PDU", "item_type": "pdu", "start_unit": 3, "unit_height": 1,
        "start_lane": 1, "width_lanes": 3, "face": "rear", "depth_mode": "half",
        "ports": [{"port_number": 1, "connector": "iec"}, {"port_number": 2, "connector": "psu"}],
    }
    front, rear = rack_dashboard.build_faces(rack, [nas, pdu], [])
    nas_block = front["blocks"][0]
    assert not nas_block["rear"] and nas_block["faceplate"] is None  # drive bays from the front
    assert nas_block["alternate"]["rear"] and nas_block["alternate"]["faceplate"]
    assert nas_block["alternate"]["image_units"] == 2
    assert [p["connector"] for p in nas_block["alternate"]["ports"]] == ["data", "psu"]
    pdu_block = rear["blocks"][0]
    assert not pdu_block["rear"] and pdu_block["alternate"]["rear"]  # outlets, then the input side
    assert [p["connector"] for p in pdu_block["alternate"]["ports"]] == ["psu"]


def test_rack_face_markup_carries_both_views():
    from pathlib import Path

    macros = (Path(__file__).resolve().parents[1] / "app/templates/infrastructure/_rack_macros.html").read_text()
    assert "--device-image-alt:" in macros and 'data-alt-view=' in macros
    assert "device_art(block, block.alternate, 'alt')" in macros


def test_device_names_switch_sits_with_device_images_and_is_remembered():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    template = (root / "app/templates/infrastructure/racks.html").read_text()
    images = template.index("data-rack-images-toggle")
    names = template.index("data-rack-names-toggle")
    assert 0 < names - images < 300
    script = (root / "app/static/js/racks.js").read_text()
    assert "racks.deviceNames" in script and "racks.deviceImages" in script


def test_full_depth_items_keep_each_elevations_view_when_swivelled():
    rack = {"id": 1, "name": "Core", "unit_count": 4, "numbering_direction": "bottom-up"}
    server = {
        "id": 7, "rack_id": 1, "name": "Web", "item_type": "server", "start_unit": 1, "unit_height": 1,
        "start_lane": 1, "width_lanes": 3, "face": "front", "depth_mode": "full", "ports": [],
    }
    front, rear = rack_dashboard.build_faces(rack, [server], [])
    # The server moves with each elevation; its front and back are never swapped.
    assert front["blocks"][0]["alternate"] is None and not front["blocks"][0]["rear"]
    assert rear["blocks"][0]["alternate"] is None and rear["blocks"][0]["rear"]
