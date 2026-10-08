"""Network map: asset type catalogue, graph building, layout and SVG output."""
import re
import xml.dom.minidom
from pathlib import Path

import pytest

from app.services import asset_types
from app.services import network_map as nm

ROOT = Path(__file__).resolve().parents[1]


def _estate():
    assets = [
        {"id": 1, "name": "Core switch", "type": "Network switch", "location": "Head office"},
        {"id": 2, "name": "FW01", "type": "Firewall", "location": "Head office", "serial_number": "FGT60F123"},
        {"id": 3, "name": "SRV-DC01", "type": "server", "location": "Head office", "os_name": "Windows Server 2022"},
        {"id": 4, "name": "HO-Bridge", "type": "airFiber 5XHD", "location": "Head office"},
        {"id": 5, "name": "WH-Bridge", "type": "airFiber 5XHD", "location": "Warehouse"},
        {"id": 6, "name": "WH-Switch", "type": "switch", "location": "Warehouse"},
        {"id": 7, "name": "WH-AP-1", "type": "UniFi AP", "location": "Warehouse"},
        {"id": 8, "name": "Reception PC", "type": "workstation", "form_factor": "Desktop", "location": "Head office"},
        {"id": 9, "name": "Laptop-7", "type": "workstation", "form_factor": "Notebook"},
        {"id": 10, "name": "Copier", "type": "Managed Printer", "location": "Head office"},
        {"id": 11, "name": "NBN NTD", "type": "modem", "location": "Head office"},
    ]
    overview = {
        "racks": [{"id": 1, "name": "Comms A", "location": "Head office", "unit_count": 24}],
        "equipment": [
            {"id": 11, "rack_id": 1, "asset_id": 2, "start_unit": 20, "item_type": "server", "ports": [
                {"id": 201, "connector": "data", "display_label": "Port 1", "peer_port_id": 101}]},
            {"id": 12, "rack_id": 1, "asset_id": 1, "start_unit": 18, "item_type": "switch", "ports": [
                {"id": 101, "connector": "data", "display_label": "Port 1", "peer_port_id": 201},
                {"id": 102, "connector": "data", "display_label": "Port 2", "peer_port_id": 301},
                {"id": 103, "connector": "data", "display_label": "Port 24", "asset_id": 4},
                {"id": 104, "connector": "data", "display_label": "Port 10", "asset_id": 10, "label": None},
                {"id": 105, "connector": "data", "display_label": "Port 11", "peer_port_id": 401}]},
            {"id": 13, "rack_id": 1, "asset_id": 3, "start_unit": 10, "item_type": "server", "ports": [
                {"id": 301, "connector": "data", "display_label": "Port 1", "peer_port_id": 102}]},
            {"id": 14, "rack_id": 1, "asset_id": None, "name": "Patch panel A", "start_unit": 19, "item_type": "patch_panel", "ports": [
                {"id": 401, "connector": "data", "display_label": "Port 1", "peer_port_id": 105, "label": "Cat6"}]},
        ],
        "networks": [{"id": 1, "name": "LAN", "cidr": "10.0.0.0/24"}],
        "addresses": [
            {"asset_id": 3, "network_id": 1, "address": "10.0.0.10"},
            {"asset_id": 2, "network_id": 1, "address": "10.0.0.1"},
            {"asset_id": 8, "network_id": 1, "address": "10.0.0.50"},
        ],
    }
    extra = {
        "assets": assets,
        "interfaces": [
            {"id": 1, "asset_id": 4, "name": "eth0", "kind": "ethernet", "ip_address": "10.0.0.2"},
            {"id": 2, "asset_id": 4, "name": "radio0", "kind": "radio", "radio_mode": "ptp_master", "frequency_mhz": 5800, "channel_width_mhz": 40, "azimuth_deg": 45},
            {"id": 3, "asset_id": 5, "name": "radio0", "kind": "radio", "radio_mode": "ptp_station", "frequency_mhz": 5800, "azimuth_deg": 225},
            {"id": 4, "asset_id": 5, "name": "eth0", "kind": "ethernet", "ip_address": "10.0.0.3"},
            {"id": 5, "asset_id": 6, "name": "Port 1", "kind": "ethernet"},
            {"id": 6, "asset_id": 6, "name": "Port 8", "kind": "ethernet"},
            {"id": 7, "asset_id": 7, "name": "eth0", "kind": "ethernet"},
            {"id": 8, "asset_id": 7, "name": "wlan0", "kind": "wifi", "radio_mode": "ap", "frequency_mhz": 5180, "ssid": "Warehouse"},
            {"id": 9, "asset_id": 11, "name": "WAN", "kind": "wan", "ip_address": "203.0.113.7"},
            {"id": 10, "asset_id": 11, "name": "LAN", "kind": "ethernet"},
            {"id": 11, "asset_id": 2, "name": "wan1", "kind": "ethernet"},
        ],
        "links": [
            {"a_kind": "interface", "a_id": 2, "b_kind": "interface", "b_id": 3, "medium": "wireless", "frequency_mhz": 5800, "distance_m": 4200, "signal_dbm": -54, "label": "HO ↔ Warehouse"},
            {"a_kind": "interface", "a_id": 4, "b_kind": "interface", "b_id": 5, "medium": "copper"},
            {"a_kind": "interface", "a_id": 6, "b_kind": "interface", "b_id": 7, "medium": "copper", "speed_mbps": 1000},
            {"a_kind": "interface", "a_id": 10, "b_kind": "interface", "b_id": 11, "medium": "copper"},
        ],
        "relationships": [],
    }
    return overview, extra


# ---------------------------------------------------------------------------
# Asset type catalogue
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("reported", "extra", "expected"), [
    # Tactical RMM reports monitoring_type plus chassis and virtualisation.
    ("workstation", {"form_factor": "Notebook"}, "laptop"),
    ("workstation", {"form_factor": "Desktop"}, "workstation"),
    ("workstation", {"machine_type": "Virtual"}, "virtual_machine"),
    ("server", {"machine_type": "Virtual"}, "server"),
    ("server", {}, "server"),
    # Syncro and free-text types.
    ("Syncro Device", {"os_name": "Windows 11 Pro"}, "workstation"),
    ("Syncro Device", {"os_name": "Windows Server 2022 Standard"}, "server"),
    ("Managed Printer", {}, "printer"),
    ("Wireless Access Point", {}, "access_point"),
    ("Ubiquiti airFiber 60 LR", {}, "wireless_bridge"),
    ("Point-to-point link", {}, "wireless_bridge"),
    ("FortiGate firewall", {}, "firewall"),
    ("NBN NTD", {}, "modem"),
    ("Patch panel", {}, "patch_panel"),
    (None, {}, "other"),
])
def test_derive_maps_synced_types_onto_the_catalogue(reported, extra, expected):
    assert asset_types.derive(reported, **extra) == expected


def test_effective_prefers_the_stored_type():
    assert asset_types.effective({"asset_type": "nvr", "type": "server"}) == "nvr"
    assert asset_types.effective({"asset_type": "bogus", "type": "server"}) == "server"
    assert asset_types.effective({"type": "workstation", "form_factor": "Laptop"}) == "laptop"


def test_every_type_has_a_self_contained_icon():
    for item in asset_types.ASSET_TYPES:
        path = ROOT / "app/static/images/asset-types" / f"{item.key}.svg"
        xml.dom.minidom.parse(str(path))
        markup = asset_types.icon_markup(item.key)
        assert markup, item.key
        # Icons are embedded as <symbol>s in exports, so ids would collide.
        assert " id=" not in markup, item.key
        assert item.category in asset_types.CATEGORIES


def test_catalogue_groups_cover_every_type_once():
    keys = [item.key for group in asset_types.grouped() for item in group["types"]]
    assert sorted(keys) == sorted(item.key for item in asset_types.ASSET_TYPES)
    assert asset_types.CUSTOM_KEY not in keys
    with pytest.raises(ValueError):
        asset_types.normalise("spaceship")
    # Custom types are named, never picked by key.
    with pytest.raises(ValueError):
        asset_types.normalise(asset_types.CUSTOM_KEY)


def test_custom_types_resolve_by_name_and_use_the_generic_icon():
    # Custom mode matches catalogue types by label or key first.
    assert asset_types.resolve_name("router", mode="custom") == ("router", "Router")
    assert asset_types.resolve_name("  Network   SWITCH ", mode="custom") == ("switch", "Network switch")
    assert asset_types.resolve_name("Forklift", mode="custom") == ("custom", "Forklift")
    # Manual mode never uses the catalogue.
    assert asset_types.resolve_name("Router", mode="manual") == ("custom", "Router")
    # An existing custom type's spelling is reused.
    assert asset_types.resolve_name("  forklift ", mode="manual", existing=["Forklift"]) == ("custom", "Forklift")
    with pytest.raises(ValueError):
        asset_types.resolve_name("   ", mode="custom")
    asset = {"asset_type": "custom", "type": "Forklift"}
    assert asset_types.effective(asset) == "custom"
    assert asset_types.display_label(asset) == "Forklift"
    assert asset_types.get("custom").icon.endswith("/other.svg")
    assert asset_types.icon_markup("custom") == asset_types.icon_markup("other")


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def _graph(**options):
    overview, extra = _estate()
    return nm.build_graph(overview, extra, nm.MapOptions(**options))


def _pairs(graph, medium=None):
    return {tuple(sorted((edge.a, edge.b))) for edge in graph.edges if medium in (None, edge.medium)}


def test_graph_joins_racks_interfaces_and_ipam():
    graph = _graph()
    # Racked assets sit in their rack; the rack item without an asset is its own node.
    assert graph.nodes["asset:2"].rack_name == "Comms A"
    assert graph.nodes["item:14"].type_key == "patch_panel"
    assert graph.nodes["asset:3"].ips == ["10.0.0.10"]
    # Rack peer ports, port-to-asset links and interface links all draw.
    assert ("asset:1", "asset:2") in _pairs(graph)
    assert ("asset:1", "asset:4") in _pairs(graph)
    assert ("asset:6", "asset:7") in _pairs(graph)
    # A WAN interface connects the modem to the internet.
    assert ("asset:11", "internet") in _pairs(graph, "wan")


def test_map_port_labels_match_the_documented_connections():
    """Each edge label must be the physical port it was drawn from.

    Regression guard for the "Router · ports and connections" panel being the
    source of truth: a router whose Port 1 is patched to the Printer and whose
    Port 2 is patched to the PoE injector must draw exactly those labels, not
    have them swapped. The labels are produced by ``_number_ports`` (the same
    code that feeds the rack dialog), so this pins the numbering and the map
    together; a stale or re-ordered render would surface as a mismatch here.
    """
    from app.repositories import infrastructure as infrastructure_repo

    # The router's ports exactly as documented: Port 1 -> Printer, Port 2 -> PoE.
    unifi_ports = infrastructure_repo._number_ports([
        {"id": 101, "port_number": 1, "connector": "data", "asset_id": 2,
         "source_port_id": None, "peer_port_id": None},
        {"id": 102, "port_number": 2, "connector": "data", "asset_id": None,
         "source_port_id": None, "peer_port_id": 201},
        {"id": 103, "port_number": 3, "connector": "psu", "asset_id": None,
         "source_port_id": None, "peer_port_id": None},
    ])
    poe_ports = infrastructure_repo._number_ports([
        {"id": 201, "port_number": 1, "connector": "data", "asset_id": None,
         "source_port_id": None, "peer_port_id": 102},
    ])
    overview = {
        "racks": [{"id": 1, "name": "Comms A", "location": "HQ", "unit_count": 24}],
        "equipment": [
            {"id": 11, "rack_id": 1, "asset_id": 1, "start_unit": 20, "item_type": "router", "ports": unifi_ports},
            {"id": 12, "rack_id": 1, "asset_id": None, "name": "PoE Injector", "start_unit": 19,
             "item_type": "other", "ports": poe_ports},
        ],
        "networks": [], "addresses": [],
    }
    extra = {
        "assets": [
            {"id": 1, "name": "RADIA Unifi Express 7", "type": "router", "location": "HQ"},
            {"id": 2, "name": "Printer", "type": "other", "location": "HQ"},
        ],
        "interfaces": [], "links": [], "relationships": [],
    }
    graph = nm.build_graph(overview, extra, nm.MapOptions(layout="topology", detail="standard"))

    def port_label_on(node_id, other_node_id):
        for edge in graph.edges:
            if {edge.a, edge.b} == {node_id, other_node_id}:
                return edge.a_port if edge.a == node_id else edge.b_port
        raise AssertionError(f"no edge between {node_id} and {other_node_id}")

    # The router side of each edge carries the label of the physical port used.
    assert port_label_on("asset:1", "asset:2") == "Port 1"   # Port 1 is patched to the Printer
    assert port_label_on("asset:1", "item:12") == "Port 2"   # Port 2 is patched to the PoE injector
    assert port_label_on("item:12", "asset:1") == "Port 1"   # ...into the injector's own Port 1
    # The hover-panel payload (graph_payload) agrees with what is drawn.
    links = {link["peer"]: link["port"] for link in nm.graph_payload(graph)["nodes"]["asset:1"]["links"]}
    assert links == {"Printer": "Port 1", "PoE Injector": "Port 2"}


def test_point_to_point_wireless_link_between_sites():
    graph = _graph()
    wireless = [edge for edge in graph.edges if edge.medium == "wireless"]
    assert len(wireless) == 1
    edge = wireless[0]
    assert {edge.a, edge.b} == {"asset:4", "asset:5"}
    assert {edge.a_port, edge.b_port} == {"radio0"}
    assert edge.details == ["5.8 GHz", "4.2 km", "-54 dBm"]
    assert graph.nodes["asset:4"].site == "Head office"
    assert graph.nodes["asset:5"].site == "Warehouse"
    # Each bridge carries one ethernet and one radio interface.
    assert graph.nodes["asset:4"].radio_count == 1
    assert [item["kind"] for item in graph.nodes["asset:5"].interfaces] == ["radio", "ethernet"]


def test_undocumented_endpoints_are_hidden_until_requested():
    assert "asset:9" not in _graph(hide_unlinked=False).nodes  # A laptop with nothing documented.
    assert "asset:9" in _graph(include_unlinked=True, hide_unlinked=False).nodes
    # Infrastructure always appears, and documented endpoints do too.
    assert "asset:8" in _graph(hide_unlinked=False).nodes  # Has an IP address.
    assert "asset:10" in _graph().nodes  # Linked from a switch port.


def test_devices_without_links_are_hidden_by_default():
    graph = _graph()
    assert "asset:8" not in graph.nodes  # Has an IP but no link to another device.
    assert all(any(node_id in (edge.a, edge.b) for edge in graph.edges) for node_id in graph.nodes)
    assert "asset:8" in _graph(hide_unlinked=False).nodes
    # Subnet membership is not a link to another device.
    assert "asset:8" not in _graph(show_subnets=True).nodes
    assert "Not linked to other devices" not in nm.render_svg(graph, title="Map")


def test_hide_unlinked_option_reads_its_checkbox():
    from starlette.datastructures import QueryParams

    assert nm.MapOptions.from_params(QueryParams("")).hide_unlinked is True
    assert nm.MapOptions.from_params(QueryParams("unlinked=show&unlinked=hide")).hide_unlinked is True
    assert nm.MapOptions.from_params(QueryParams("unlinked=show")).hide_unlinked is False
    assert ("unlinked", "show") in nm.MapOptions(hide_unlinked=False).query()
    assert all(key != "unlinked" for key, _value in nm.MapOptions().query())


def test_type_filter_drops_devices_and_their_links():
    graph = _graph(types=frozenset({"wireless_bridge", "switch"}))
    kinds = {node.type_key for node in graph.nodes.values() if node.kind in {"asset", "item"}}
    assert kinds == {"wireless_bridge", "switch"}
    assert all(edge.a in graph.nodes and edge.b in graph.nodes for edge in graph.edges)
    assert "internet" not in graph.nodes  # Nothing left links to it.


def test_site_filter_keeps_neighbours_across_sites():
    graph = _graph(site="Warehouse")
    assert {"asset:5", "asset:6", "asset:7"} <= set(graph.nodes)
    assert "asset:4" in graph.nodes  # The far end of the wireless link.
    assert "asset:3" not in graph.nodes


def test_subnets_only_draw_when_requested():
    assert not any(node.kind == "network" for node in _graph().nodes.values())
    graph = _graph(show_subnets=True)
    assert ("asset:3", "network:1") in _pairs(graph, "subnet")


def test_links_to_removed_endpoints_are_ignored():
    overview, extra = _estate()
    extra["links"].append({"a_kind": "interface", "a_id": 999, "b_kind": "rack_port", "b_id": 101,
                           "medium": "copper"})
    graph = nm.build_graph(overview, extra, nm.MapOptions())
    assert len(graph.edges) == len(_graph().edges)


def test_options_round_trip_through_query_parameters():
    from starlette.datastructures import QueryParams

    options = nm.MapOptions(detail="detailed", types=frozenset({"switch", "router"}), show_subnets=True,
                            include_unlinked=True, site="Warehouse", show_racks=False, layout="sites",
                            hide_unlinked=False)
    parsed = nm.MapOptions.from_params(QueryParams(options.query()))
    assert parsed == options
    assert nm.MapOptions.from_params(QueryParams("detail=bogus&types=nope")).detail == nm.DEFAULT_DETAIL
    assert nm.MapOptions.from_params(QueryParams("types=nope")).types == frozenset()


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("detail", list(nm.DETAIL_LEVELS))
def test_svg_is_well_formed_and_self_contained(detail):
    graph = _graph(detail=detail, show_subnets=True)
    svg = nm.render_svg(graph, title="Network map <Acme>", subtitle="Acme & Co")
    xml.dom.minidom.parseString(svg)
    assert "&lt;Acme&gt;" in svg
    # No external references: PNG export rasterises this in a canvas.
    assert "http://" not in svg.replace("http://www.w3.org", "")
    assert 'href="/' not in svg.split("</defs>")[0]
    for key in {node.type_key for node in graph.nodes.values()}:
        assert f'id="nm-icon-{key}"' in svg
    # Fonts are presentation attributes so WeasyPrint sizes text correctly.
    assert "<style" not in svg
    assert 'font-size="11.5"' in svg


def test_custom_asset_types_draw_with_their_name_and_generic_icon():
    overview, extra = _estate()
    extra["assets"].append({"id": 99, "name": "Forklift 3", "type": "Forklift",
                            "asset_type": "custom", "location": "Warehouse"})
    extra["relationships"] = list(extra.get("relationships") or []) + [
        {"source_asset_id": 99, "target_id": 6}]
    graph = nm.build_graph(overview, extra, nm.MapOptions(detail="standard"))
    node = graph.nodes["asset:99"]
    assert node.type_key == "custom" and node.type_label == "Forklift"
    svg = nm.render_svg(graph, title="Map")
    xml.dom.minidom.parseString(svg)
    assert 'id="nm-icon-custom"' in svg
    assert "Forklift" in svg


def test_detail_levels_add_information():
    overview = nm.render_svg(_graph(detail="overview"), title="Map")
    standard = nm.render_svg(_graph(detail="standard"), title="Map")
    detailed = nm.render_svg(_graph(detail="detailed"), title="Map")
    assert "10.0.0.10" not in overview and "10.0.0.10" in standard
    assert "5.8 GHz" not in overview and "5.8 GHz" in standard
    assert "Serial: FGT60F123" in detailed and "Serial: FGT60F123" not in standard


def test_wireless_links_are_dashed_and_labelled():
    svg = nm.render_svg(_graph(layout="sites"), title="Map")
    edge = re.search(r'<g class="nm-edge"[^>]*data-medium="wireless".*?</g>', svg).group(0)
    assert 'stroke-dasharray="9 6"' in edge
    assert "5.8 GHz · 4.2 km · -54 dBm · HO ↔ Warehouse" in svg


def test_interactive_svg_leaves_navigation_to_the_page():
    assert "<a href=" in nm.render_svg(_graph(), title="Map")
    assert "<a href=" not in nm.render_svg(_graph(), title="Map", interactive=True)


def test_layout_keeps_cards_apart():
    graph = _graph(detail="detailed", include_unlinked=True, hide_unlinked=False)
    nm.layout(graph)
    cards = [(node.x, node.y, node.x + nm.CARD_W, node.y + node.h) for node in graph.nodes.values()]
    for index, (ax1, ay1, ax2, ay2) in enumerate(cards):
        for bx1, by1, bx2, by2 in cards[index + 1:]:
            assert ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1


def test_inventory_lists_devices_with_their_links():
    rows = nm.inventory(_graph())
    bridge = next(row for row in rows if row["name"] == "HO-Bridge")
    assert bridge["site"] == "Head office"
    assert any(link["peer"] == "WH-Bridge" and link["medium"] == "Wireless" for link in bridge["links"])


# ---------------------------------------------------------------------------
# Topology layout
# ---------------------------------------------------------------------------

def test_topology_tree_runs_from_the_internet_outwards():
    graph = _graph()
    roots, children = nm.spanning_forest(graph)
    assert roots[0] == "internet"
    assert children["internet"] == ["asset:11"]
    # The wireless bridge pair chains on: HO bridge -> WH bridge -> WH switch -> AP.
    assert "asset:5" in children["asset:4"]
    assert "asset:6" in children["asset:5"]
    assert "asset:7" in children["asset:6"]


def test_topology_places_each_device_right_of_its_uplink():
    graph = _graph()
    nm.layout(graph)
    _roots, children = nm.spanning_forest(graph)
    for parent, kids in children.items():
        for kid in kids:
            assert graph.nodes[kid].x > graph.nodes[parent].x
        # A parent sits level with the middle of its children.
        ys = [graph.nodes[kid].y for kid in kids]
        assert min(ys) <= graph.nodes[parent].y <= max(ys)


def test_topology_fans_out_multiple_links_from_one_device():
    """Several links leaving one device must fan their port labels apart.

    Regression guard for the uplink labels stacking on a single point: a
    switch with four downlinks used to draw all four of its port names at the
    same x (the icon's right edge), overlapping into an unreadable column.
    They now spread out along their curves to distinct x-coordinates.
    """
    overview = {
        "racks": [{"id": 1, "name": "Core", "location": "HQ", "unit_count": 42}],
        "equipment": [{"id": 1, "rack_id": 1, "asset_id": 1, "start_unit": 30,
                       "item_type": "switch", "ports": [
                           {"id": 10, "connector": "data", "display_label": "Port 1", "asset_id": 2},
                           {"id": 11, "connector": "data", "display_label": "Port 2", "asset_id": 3},
                           {"id": 12, "connector": "data", "display_label": "Port 3", "asset_id": 4},
                           {"id": 13, "connector": "data", "display_label": "Port 4", "asset_id": 5}]}],
        "networks": [], "addresses": [],
    }
    extra = {
        "assets": [
            {"id": 1, "name": "Core Switch", "type": "switch", "location": "HQ"},
            {"id": 2, "name": "AP 1", "type": "access_point", "location": "HQ"},
            {"id": 3, "name": "NVR", "type": "nvr", "location": "HQ"},
            {"id": 4, "name": "Printer", "type": "other", "location": "HQ"},
            {"id": 5, "name": "Workstation", "type": "workstation", "location": "HQ"},
        ],
        "interfaces": [], "links": [], "relationships": [],
    }
    graph = nm.build_graph(overview, extra, nm.MapOptions(layout="topology", detail="standard"))
    svg = nm.render_svg(graph, title="Map")

    # The switch is the uplink (leftmost device), so its four port labels sit
    # on the icon's right side and are drawn start-anchored. Their x values
    # must be distinct -- not stacked on one point.
    labels = re.findall(
        r'<text class="nm-port"[^>]*?x="([\d.-]+)" y="([\d.-]+)" text-anchor="(\w+)"[^>]*>([^<]*)</text>',
        svg)
    switch_x = sorted(float(x) for x, _y, anchor, _text in labels if anchor == "start")
    assert len(switch_x) == 4
    assert len({round(x, 1) for x in switch_x}) == 4, f"uplink labels stacked on one x: {switch_x}"


def test_topology_lists_unlinked_devices_separately():
    graph = _graph(include_unlinked=True, hide_unlinked=False)
    svg = nm.render_svg(graph, title="Map")
    assert "Not linked to other devices" in svg
    linked = [node for node in graph.nodes.values() if node.id in {"asset:1", "asset:7"}]
    laptop = graph.nodes["asset:9"]
    assert all(laptop.y > node.y for node in linked)


def test_topology_links_are_smooth_curves():
    svg = nm.render_svg(_graph(), title="Map")
    paths = re.findall(r'<g class="nm-edge"[^>]*>.*?<path d="([^"]+)"', svg)
    assert paths and all(" C" in path for path in paths)


@pytest.mark.parametrize("detail", ["standard", "detailed"])
@pytest.mark.parametrize("reverse_links", [False, True])
@pytest.mark.parametrize("reverse_endpoints", [False, True])
@pytest.mark.parametrize("mirror", [False, True])
def test_topology_port_labels_follow_their_connections(monkeypatch, detail, reverse_links,
                                                       reverse_endpoints, mirror):
    extra = {
        "assets": [
            {"id": 1, "name": "RADIA Unifi Express 7", "asset_type": "router"},
            {"id": 2, "name": "Printer", "asset_type": "other"},
            {"id": 3, "name": "PoE Injector", "asset_type": "other"},
            {"id": 4, "name": "RADIA Teltonika OTD500", "asset_type": "modem"},
        ],
        "interfaces": [
            {"id": 1, "asset_id": 1, "name": "Port 1", "kind": "ethernet"},
            {"id": 2, "asset_id": 1, "name": "Port 2", "kind": "ethernet"},
            {"id": 3, "asset_id": 2, "name": "Port 1", "kind": "ethernet"},
            {"id": 4, "asset_id": 3, "name": "Port 1", "kind": "ethernet"},
            {"id": 5, "asset_id": 3, "name": "Port 2", "kind": "ethernet"},
            {"id": 6, "asset_id": 4, "name": "Port 1", "kind": "ethernet"},
        ],
        "links": [
            {"a_kind": "interface", "a_id": 1, "b_kind": "interface", "b_id": 3, "medium": "copper"},
            {"a_kind": "interface", "a_id": 2, "b_kind": "interface", "b_id": 4,
             "medium": "copper", "label": "<-->"},
            {"a_kind": "interface", "a_id": 5, "b_kind": "interface", "b_id": 6, "medium": "copper"},
        ],
    }
    if reverse_links:
        extra["links"].reverse()
    if reverse_endpoints:
        for link in extra["links"]:
            link["a_id"], link["b_id"] = link["b_id"], link["a_id"]
    graph = nm.build_graph({}, extra, nm.MapOptions(detail=detail))
    if mirror:
        original_layout = nm.layout

        def mirrored_layout(graph):
            width, height, boxes = original_layout(graph)
            for node in graph.nodes.values():
                node.x = width - node.x - nm.CARD_W
            return width, height, boxes

        monkeypatch.setattr(nm, "layout", mirrored_layout)
    svg = xml.dom.minidom.parseString(nm.render_svg(graph, title="Map"))
    texts = svg.getElementsByTagName("text")
    router = graph.nodes["asset:1"]
    injector = graph.nodes["asset:3"]
    printer = graph.nodes["asset:2"]
    assert injector.y < printer.y
    direction = -1 if mirror else 1
    router_port_anchor = "end" if mirror else "start"
    branch_left, branch_right = sorted((router.anchor[0], injector.anchor[0]))
    router_ports = {
        text.firstChild.data: (float(text.getAttribute("x")), float(text.getAttribute("y")))
        for text in texts if text.getAttribute("class") == "nm-port"
        and text.getAttribute("text-anchor") == router_port_anchor
        and branch_left < float(text.getAttribute("x")) < branch_right
    }
    # Port 2 must label the upper injector branch; Port 1 the lower printer branch.
    assert router_ports["Port 2"][1] < router_ports["Port 1"][1]
    payload = nm.graph_payload(graph)["nodes"]
    assert {link["port"]: (link["peer"], link["peer_port"])
            for link in payload[router.id]["links"]} == {
        "Port 1": ("Printer", "Port 1"), "Port 2": ("PoE Injector", "Port 1"),
    }
    # Each remote Port 1 stays beside its own device, regardless of endpoint order.
    for peer in (injector, printer):
        peer_port_x = peer.anchor[0] - direction * (nm.ICON / 2 + 8)
        peer_port_y = peer.anchor[1] - (nm.TOPO_LINE_H / 2 if peer is injector and reverse_endpoints else 0)
        assert any(text.getAttribute("class") == "nm-port" and text.firstChild.data == "Port 1"
                   and float(text.getAttribute("x")) == peer_port_x
                   and float(text.getAttribute("y")) == peer_port_y for text in texts)
    # Link annotations remain immediately below the port at their owning endpoint.
    if reverse_endpoints:
        owner_port_x = injector.anchor[0] - direction * (nm.ICON / 2 + 8)
        owner_port_y = injector.anchor[1] - nm.TOPO_LINE_H / 2
    else:
        owner_port_x, owner_port_y = router_ports["Port 2"]
    assert any(text.getAttribute("class") == "nm-edge-label" and text.firstChild.data == "<-->"
               and float(text.getAttribute("x")) == owner_port_x
               and float(text.getAttribute("y")) == owner_port_y + nm.TOPO_LINE_H for text in texts)


def test_sites_layout_still_groups_by_site_and_rack():
    svg = nm.render_svg(_graph(layout="sites"), title="Map")
    assert 'class="nm-site"' in svg and 'class="nm-rack"' in svg
    assert 'class="nm-site"' not in nm.render_svg(_graph(), title="Map")
