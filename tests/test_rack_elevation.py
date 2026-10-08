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
    assert 'aria-label="Show ports for Two U server, front, units 3 to 4, lanes 1 to 3"' in html
    assert html.count('class="rack__device-edit"') == 2
    assert f'style="grid-row: {row}; grid-column: 3" data-edit-equipment="7"' in html


def test_edit_pencil_is_only_offered_to_editors():
    rack = {"id": 1, "name": "Core rack", "unit_count": 4, "numbering_direction": "bottom-up"}
    html = _render_face(rack, [_item(depth_mode="half")], [], can_edit=False)
    assert 'data-inspect-placement="7"' in html
    assert "rack__device-edit" not in html


def test_clicking_an_item_opens_a_ports_table_dialog():
    modals = TEMPLATE[TEMPLATE.index("{% block modals %}"):]
    ports = modals[:modals.index("</dialog>")]
    assert '<dialog id="rack-ports-dialog"' in ports and "data-ports-body" in ports
    assert "{% if can_edit %}" not in modals[:modals.index('<dialog id="rack-ports-dialog"')]
    assert "data-ports-edit" in ports
    assert '<template data-port-table="{{ item.id }}"' in TEMPLATE
    assert '<th scope="col">Connected to</th>' in TEMPLATE
    script = (ROOT / "app/static/js/racks.js").read_text()
    assert "addEventListener('click', () => showPorts(button))" in script
    assert script.index("const showPorts") < script.index("if (!form || !placeDialog) return;")


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


def test_switch_draws_ports_and_linked_or_labelled_ports_are_lit():
    rack = {"id": 1, "name": "Core rack", "unit_count": 4, "numbering_direction": "bottom-up"}
    ports = [{"port_number": n, "connector": "data", "asset_id": 9 if n == 2 else None,
              "label": "Printer" if n == 3 else None} for n in range(1, 5)]
    switch = _item(item_type="switch", unit_height=1, start_unit=1, depth_mode="half", ports=ports)

    html = _render_face(rack, [switch], [])

    assert 'class="rack__ports"' in html
    assert html.count('class="is-data is-linked"') == 2
    assert html.count('class="is-data"') == 2


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
    dialog = TEMPLATE[TEMPLATE.index('<dialog id="rack-place-dialog"'):TEMPLATE.index('<dialog id="rack-reservation-dialog"')]
    for name in ("entry_kind", "item_type", "face", "depth_mode", "width_lanes", "start_lane"):
        assert f'type="radio" name="{name}"' in dialog
    for name in ("rack_id", "name", "asset_id", "power_draw_watts", "label", "owner",
                 "start_unit", "unit_height", "notes"):
        assert f'name="{name}"' in dialog
    assert 'name="port_count_{{ key }}"' in dialog
    assert 'value="{{ value }}"{% if value == 3 %} checked{% endif %}' in dialog
    assert "data-placement-map" in dialog and "data-placement-preview" in dialog
    script = (ROOT / "app/static/js/racks.js").read_text()
    assert "const renderMap" in script
    assert "const clampLanes" in script


def test_catalogue_lists_every_asset_type_plus_rack_hardware_with_images():
    from app.services import asset_types, rack_item_types

    keys = [item_type.key for item_type in rack_item_types.ITEM_TYPES]
    assert keys == [item.key for item in asset_types.ASSET_TYPES] + ["kvm", "fan_tray", "shelf", "cable_management", "poe_injector"]
    for item in asset_types.ASSET_TYPES:
        assert rack_item_types.get(item.key).label == item.label
        assert rack_item_types.get(item.key).asset_type == item.key
    assert rack_item_types.get("kvm").asset_type == "other"
    groups = rack_item_types.grouped()
    assert [group["label"] for group in groups][-1] == "Rack hardware"
    assert sum(len(group["types"]) for group in groups) == len(keys)
    assert rack_item_types.for_asset({"asset_type": "firewall"}) == "firewall"
    assert rack_item_types.for_asset({"type": "Managed Printer"}) == "printer"
    assert all(len(key) <= 24 for key in keys)  # rack_equipment.item_type is VARCHAR(24)
    for item_type in rack_item_types.ITEM_TYPES:
        for width in (1, 2, 3):
            for rear in (False, True):
                image = rack_item_types.image_path(item_type.key, width, rear)
                assert (ROOT / "app" / image.lstrip("/")).is_file(), image
    assert rack_item_types.get("device").key == "server"
    connectors = {item_type.key: item_type.default_counts for item_type in rack_item_types.ITEM_TYPES}
    assert connectors["server"] == {"data": 2, "psu": 2}
    assert connectors["switch"] == {"data": 24, "psu": 1}
    assert connectors["storage"] == {"data": 4, "psu": 2}
    assert connectors["kvm"] == {"kvm": 8, "data": 1, "psu": 1}
    assert connectors["fan_tray"] == {"psu": 1}
    assert connectors["pdu"] == {"iec": 8, "3pin": 0, "psu": 1}
    assert connectors["ups"] == {"iec": 6, "3pin": 2, "psu": 1}
    assert connectors["poe_injector"] == {"data": 2, "psu": 1}
    assert not rack_item_types.get("shelf").power_input and rack_item_types.get("server").power_input
    assert rack_item_types.connector_label("3pin", 2) == "3-pin 2"


def test_passive_items_have_no_status_led_and_pdus_draw_outlets():
    rack = {"id": 1, "name": "Core rack", "unit_count": 4, "numbering_direction": "bottom-up"}
    blank = _item(id=1, item_type="shelf", name=None, start_unit=1, unit_height=1, depth_mode="half")
    pdu = _item(id=2, item_type="pdu", name="PDU A", start_unit=2, unit_height=1, depth_mode="half")

    html = _render_face(rack, [blank, pdu], [])

    assert "Shelf or blanking panel" in html
    assert html.count('class="rack__led ') == 1
    assert "rack__device--pdu" in html and html.count('<i class="is-iec"') == 8


def test_device_images_toggle_is_on_by_default_and_remembered():
    assert 'rack-workspace has-device-images' in TEMPLATE
    assert 'data-rack-images-toggle aria-pressed="true"' in TEMPLATE
    script = (ROOT / "app/static/js/racks.js").read_text()
    assert "racks.deviceImages" in script


def test_editing_reuses_the_add_dialog_with_every_option():
    assert '<dialog id="rack-edit-dialog"' not in TEMPLATE
    assert 'id="rack-items-data"' in TEMPLATE
    dialog = TEMPLATE[TEMPLATE.index('<dialog id="rack-place-dialog"'):TEMPLATE.index('<dialog id="rack-reservation-dialog"')]
    assert "data-edit-remove" in dialog and "data-port-links" in dialog and "data-outlet-options" in dialog
    script = (ROOT / "app/static/js/racks.js").read_text()
    assert "const editEquipment" in script
    assert "/edit`" in script
    assert "blocksOn(rack, side, editingId)" in script
    reservation = TEMPLATE[TEMPLATE.index('<dialog id="rack-reservation-dialog"'):]
    assert "data-reservation-map" in reservation


def test_full_depth_items_show_their_rear_panel_on_the_opposite_face():
    rack = {"id": 1, "name": "Core rack", "unit_count": 4, "numbering_direction": "bottom-up"}
    ups_ports = [{"port_number": n, "connector": "iec" if n <= 2 else "3pin", "asset_id": None}
                 for n in range(1, 4)]
    ups = _item(item_type="ups", start_unit=1, unit_height=2, width_lanes=2, ports=ups_ports)
    front, rear = rack_dashboard.build_faces(rack, [ups], [])

    front_block, rear_block = front["blocks"][0], rear["blocks"][0]
    assert front_block["image"] == "/static/images/racks/ups-w2.svg" and not front_block["rear"]
    assert rear_block["image"] == "/static/images/racks/ups-w2-rear.svg" and rear_block["rear"]
    # A UPS's outlets are on its back panel.
    assert front_block["ports"] == []
    assert [port["connector"] for port in rear_block["ports"]] == ["iec", "iec", "3pin"]


def test_edit_payload_carries_position_counts_links_and_psu_sources():
    item = _item(item_type="pdu",
                 ports=[{"port_number": 1, "connector": "iec", "ordinal": 1, "asset_id": 5, "label": None},
                        {"port_number": 2, "connector": "3pin", "ordinal": 1, "asset_id": None, "label": "Kettle"},
                        {"port_number": 3, "connector": "psu", "ordinal": 1, "asset_id": None,
                         "label": "Wall B2", "source_port_id": 41}])
    payload = rack_dashboard.edit_payload(item)
    assert payload["port_counts"] == {"iec": 1, "3pin": 1, "psu": 1}
    assert payload["ports"][1]["label"] == "Kettle"
    assert payload["ports"][2] == {"connector": "psu", "ordinal": 1, "asset_id": None,
                                   "label": "Wall B2", "source_port_id": 41, "id": None,
                                   "peer_port_id": None, "fed_port_id": None, "network_peer": None}
    assert (payload["start_unit"], payload["width_lanes"], payload["depth_mode"]) == (3, 3, "full")


def test_server_network_ports_and_psus_are_drawn_on_the_rear_only():
    rack = {"id": 1, "name": "Core rack", "unit_count": 4, "numbering_direction": "bottom-up"}
    server = _item(item_type="server", start_unit=1, unit_height=1)
    front, rear = rack_dashboard.build_faces(rack, [server], [])
    assert front["blocks"][0]["ports"] == []
    assert [port["connector"] for port in rear["blocks"][0]["ports"]] == ["data", "data", "psu", "psu"]


def test_item_list_section_remembers_expanded_state():
    assert '<details class="rack__details" id="rack-list-{{ list_rack.id }}"' in TEMPLATE
    script = (ROOT / "app/static/js/racks.js").read_text()
    assert "racks.itemListOpen" in script
    assert "details.addEventListener('toggle'" in script


def test_saving_returns_to_the_previous_scroll_position_not_an_anchor():
    routes = (ROOT / "app/features/assets/routes.py").read_text()
    assert 'anchor=f"#placement-' not in routes and 'anchor=f"#reservation-' not in routes
    script = (ROOT / "app/static/js/racks.js").read_text()
    assert "racks.scrollAfterSave" in script
    assert "document.addEventListener('submit'" in script
