"""Rendered rack elevations keep physical position and free-space actions aligned."""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from app.services import rack_dashboard


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
TEMPLATE = (TEMPLATES / "infrastructure" / "racks.html").read_text()


def _render_face(rack, equipment, reservations, face_index=0, can_edit=True):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    macros = env.get_template("infrastructure/_rack_macros.html").module
    face = rack_dashboard.build_faces(rack, equipment, reservations)[face_index]
    return str(macros.rack_face(rack, face, can_edit))


def _item(**overrides):
    item = {
        "id": 7, "rack_id": 1, "name": "Two U server", "asset_name": None,
        "item_type": "device", "start_unit": 3, "unit_height": 2,
        "start_lane": 1, "width_lanes": 3, "face": "front", "depth_mode": "full",
        "power_draw_watts": None, "port_count": 0, "ports": [],
    }
    item.update(overrides)
    return item


@pytest.mark.parametrize(
    ("direction", "row"),
    [("bottom-up", 5), ("top-down", 3)],
)
def test_full_depth_item_is_one_spanning_block_on_each_face(direction, row):
    rack = {"id": 1, "name": "Core rack", "unit_count": 8, "numbering_direction": direction}
    item = _item()

    html = _render_face(rack, [item], []) + _render_face(rack, [item], [], face_index=1)

    assert html.count('data-inspect-placement="7"') == 2
    assert html.count(f"grid-row: {row} / span 2; grid-column: 1 / span 3") == 2
    assert html.count("data-place-open") == 36
    assert 'aria-label="Edit Two U server, front, units 3 to 4, lanes 1 to 3"' in html


def test_reservation_occupies_only_its_face_and_lanes():
    rack = {"id": 1, "name": "Core rack", "unit_count": 4, "numbering_direction": "bottom-up"}
    reservation = {
        "id": 4, "rack_id": 1, "label": "Future switch", "start_unit": 2,
        "unit_height": 1, "start_lane": 2, "width_lanes": 2,
        "face": "rear", "depth_mode": "half",
    }

    html = _render_face(rack, [], [reservation]) + _render_face(rack, [], [reservation], face_index=1)

    assert html.count('data-inspect-reservation="4"') == 1
    assert "grid-row: 3 / span 1; grid-column: 2 / span 2" in html
    assert html.count("data-place-open") == 22


def test_rail_numbers_follow_mounting_direction():
    rack = {"id": 1, "name": "Main rack", "unit_count": 27, "numbering_direction": "bottom-up"}
    assert rack_dashboard.unit_rows(rack)[:2] == [27, 26]
    assert rack_dashboard.unit_rows({**rack, "numbering_direction": "top-down"})[:2] == [1, 2]


def test_switch_draws_ports_and_linked_ports_are_lit():
    rack = {"id": 1, "name": "Core rack", "unit_count": 4, "numbering_direction": "bottom-up"}
    switch = _item(item_type="switch", unit_height=1, start_unit=1, depth_mode="half",
                   port_count=4, ports=[{"port_number": n, "asset_id": 9 if n == 2 else None}
                                        for n in range(1, 5)])

    html = _render_face(rack, [switch], [])

    assert 'class="rack__ports"' in html
    assert html.count("<i") - html.count('class="is-linked"') == 3
    assert html.count('class="is-linked"') == 1


def test_warning_asset_status_is_announced_not_colour_only():
    rack = {"id": 1, "name": "Core rack", "unit_count": 4, "numbering_direction": "bottom-up"}
    item = _item(asset_status="Offline", unit_height=1, start_unit=1)

    html = _render_face(rack, [item], [])

    assert "rack__led--warning" in html
    assert "needs attention" in html
    assert rack_dashboard.equipment_status({"asset_status": "In service"}) == "active"


def test_heatmap_uses_recorded_power_density_when_available():
    rack = {"id": 1, "unit_count": 4}
    equipment = [
        _item(id=1, start_unit=1, unit_height=1, width_lanes=3, depth_mode="half", power_draw_watts=300),
        _item(id=2, start_unit=2, unit_height=1, width_lanes=1, depth_mode="half", power_draw_watts=None),
    ]
    reservations = [{"id": 3, "rack_id": 1, "start_unit": 4, "unit_height": 1, "start_lane": 1,
                     "width_lanes": 1, "face": "rear", "depth_mode": "half"}]

    heat = rack_dashboard.build_heatmap(rack, equipment, reservations)

    assert heat["mode"] == "power"
    assert heat["peak_watts"] == 100.0
    front_left = heat["rows"][0]["cells"]
    assert front_left[0]["level"] == rack_dashboard.HEAT_LEVELS
    assert front_left[1]["state"] == "unmetered"
    assert front_left[2]["state"] == "open"
    assert heat["rows"][3]["cells"][3]["state"] == "reserved"


def test_heatmap_falls_back_to_occupancy_without_power_readings():
    rack = {"id": 1, "unit_count": 2}
    heat = rack_dashboard.build_heatmap(rack, [_item(start_unit=1, unit_height=1)], [])
    assert heat["mode"] == "occupancy"
    assert heat["rows"][0]["cells"][0]["state"] == "installed"
    assert heat["rows"][0]["cells"][1]["state"] == "open"


def test_workspace_selects_requested_rack_and_normalises_view():
    data = {"racks": [{"id": 1, "unit_count": 4, "name": "A"}, {"id": 2, "unit_count": 4, "name": "B"}],
            "equipment": [], "reservations": []}

    assert rack_dashboard.build_workspace(data, "2", None)["selected_rack"]["id"] == 2
    assert rack_dashboard.build_workspace(data, "2", "overview")["view"] == "overview"
    assert rack_dashboard.normalise_view("classic") == "graphical"
    fallback = rack_dashboard.build_workspace(data, "999", "<script>")
    assert fallback["selected_rack"]["id"] == 1
    assert fallback["view"] == "graphical"
    assert rack_dashboard.build_workspace({"racks": []}, None, None)["selected_rack"] is None


def test_graphical_dashboard_exposes_capacity_heatmap_and_swivel_controls():
    assert 'class="rack-dashboard"' in TEMPLATE
    assert "data-rack-swivel" in TEMPLATE
    assert "draw.donut(rack.occupied_percent, 'Installed Capacity'" in TEMPLATE
    assert "draw.heatmap(rack, ws.heatmap)" in TEMPLATE
    assert "data-rack-sidebar-toggle" in TEMPLATE
    assert "data-rack-jump" in TEMPLATE
    assert "view=overview" in TEMPLATE
    assert "view=classic" not in TEMPLATE
    assert "rack-segmented" not in TEMPLATE


def test_graphical_dashboard_interactions_are_keyboard_accessible():
    script = (ROOT / "app/static/js/racks.js").read_text()
    assert "button.setAttribute('aria-pressed', String(swivelled))" in script
    assert "button.addEventListener('focus'" in script
    assert "sidebarToggle?.setAttribute('aria-expanded'" in script


def test_add_rack_space_dialog_uses_visual_choices_and_live_preview():
    dialog = TEMPLATE[TEMPLATE.index('<dialog id="rack-place-dialog"'):TEMPLATE.index('<dialog id="rack-edit-dialog"')]
    for name in ("entry_kind", "item_type", "face", "depth_mode", "width_lanes", "start_lane"):
        assert f'type="radio" name="{name}"' in dialog
    for name in ("rack_id", "name", "asset_id", "port_count", "power_draw_watts", "label", "owner",
                 "start_unit", "unit_height", "notes"):
        assert f'name="{name}"' in dialog
    assert "data-placement-map" in dialog
    assert "data-placement-preview" in dialog
    script = (ROOT / "app/static/js/racks.js").read_text()
    assert "const renderMap" in script
    assert "const clampLanes" in script


def test_catalogue_lists_the_default_rack_item_types_with_images():
    from app.services import rack_item_types

    labels = [item_type.label for item_type in rack_item_types.ITEM_TYPES]
    assert labels == [
        "Server", "Network switch or router", "Storage array (SAN/NAS)", "Patch panel",
        "KVM console", "Power distribution unit (PDU)", "Uninterruptible power supply (UPS)",
        "Fan tray or ventilation", "Shelf or blanking panel", "Cable management ring or bar",
    ]
    css = (ROOT / "app/static/css/infrastructure.css").read_text()
    for item_type in rack_item_types.ITEM_TYPES:
        assert (ROOT / "app" / item_type.image.lstrip("/")).is_file()
        assert f'url("../images/racks/{item_type.key}.svg")' in css
    assert rack_item_types.get("device").key == "server"
    assert rack_item_types.PORT_TYPE_KEYS == {"switch", "patch_panel", "pdu"}


def test_passive_items_have_no_status_led_and_pdus_draw_outlets():
    rack = {"id": 1, "name": "Core rack", "unit_count": 4, "numbering_direction": "bottom-up"}
    blank = _item(id=1, item_type="shelf", name=None, start_unit=1, unit_height=1, depth_mode="half")
    pdu = _item(id=2, item_type="pdu", name="PDU A", start_unit=2, unit_height=1, depth_mode="half")

    html = _render_face(rack, [blank, pdu], [])

    assert "Shelf or blanking panel" in html
    assert html.count('class="rack__led ') == 1
    assert "rack__device--pdu" in html and html.count("<i") == 8


def test_device_images_toggle_is_on_by_default_and_remembered():
    assert 'rack-workspace has-device-images' in TEMPLATE
    assert 'data-rack-images-toggle aria-pressed="true"' in TEMPLATE
    script = (ROOT / "app/static/js/racks.js").read_text()
    assert "racks.deviceImages" in script
