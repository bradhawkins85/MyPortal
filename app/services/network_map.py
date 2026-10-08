"""Build, lay out and draw a company's network map.

The map joins three sources that already describe the network:

* **Racks** - racked equipment and the patching between rack ports.
* **IPAM** - which addresses (and so which subnets) each asset holds.
* **Assets** - every device, typed from the fixed asset type catalogue, with
  its network and radio interfaces and the links documented between them.

Devices are grouped by site (a rack's location, else the asset's location)
and, inside a site, by rack. Devices outside racks are laid out in rows from
the internet edge down to endpoints. Wireless links, including long-range
point-to-point bridges between sites, are drawn as dashed radio links
labelled with their frequency, distance and signal.

Everything is rendered server-side to one SVG so the page, the PNG export
(rasterised in the browser) and the PDF export all show the same drawing.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from xml.sax.saxutils import escape

from app.repositories import network_map as links_repo
from app.services import asset_types, rack_item_types

DETAIL_LEVELS: dict[str, dict[str, str]] = {
    "overview": {"label": "Overview", "help": "Device icons and names with the links between them."},
    "standard": {"label": "Standard", "help": "Adds device types, IP addresses, port names and wireless link details."},
    "detailed": {"label": "Detailed", "help": "Adds every interface, radio settings, serial numbers, a device inventory and identification images in PDF exports."},
}
DEFAULT_DETAIL = "standard"
LAYOUTS: dict[str, dict[str, str]] = {
    "topology": {"label": "Topology", "help": "Devices fan out from the internet edge, each beside the device it connects through."},
    "sites": {"label": "Sites and racks", "help": "Devices grouped into boxes by site and rack."},
}
DEFAULT_LAYOUT = "topology"
# Devices in these categories are the network itself, so they are always
# drawn. Computers, printers and similar are drawn once they are documented
# on the network (racked, addressed, given an interface or linked), or when
# the technician asks for every device.
INFRASTRUCTURE_CATEGORIES = frozenset({"edge", "security", "network", "wireless", "server"})
UNPLACED_SITE = "Unassigned location"
INTERNET_ID = "internet"
RACK_ONLY_SKIPPED = frozenset({"shelf", "cable_management", "fan_tray"})

MEDIUM_STYLES: dict[str, dict[str, Any]] = {
    "copper": {"label": "Copper", "colour": "#475569", "width": 1.8, "dash": None},
    "fibre": {"label": "Fibre", "colour": "#ea580c", "width": 2.4, "dash": None},
    "dac": {"label": "Direct-attach", "colour": "#0f766e", "width": 2.2, "dash": None},
    "wireless": {"label": "Wireless", "colour": "#2563eb", "width": 2.6, "dash": "9 6"},
    "vpn": {"label": "VPN / tunnel", "colour": "#7c3aed", "width": 1.8, "dash": "3 4"},
    "wan": {"label": "Internet", "colour": "#7c3aed", "width": 2.2, "dash": None},
    "other": {"label": "Other", "colour": "#64748b", "width": 1.6, "dash": "6 3"},
    "relationship": {"label": "Documented connection", "colour": "#94a3b8", "width": 1.4, "dash": "2 3"},
    "subnet": {"label": "Subnet membership", "colour": "#a3a3a3", "width": 1.0, "dash": "1 3"},
}

# Layout metrics (SVG user units, ~1px on screen).
CELL_W = 196
LANE_W = 12
CARD_W = 150
ICON = 40
LINE_H = 13
# Vertical pitch between stacked port names and link labels in the topology
# view. Wider than LINE_H because those labels carry a white halo (see
# _label) that would otherwise make two adjacent lines merge into one blob.
TOPO_LINE_H = 16
SITE_PAD = 18
SITE_HEADER = 34
RACK_HEADER = 26
GAP = 26
MAX_ROW_WIDTH = 2600
MARGIN = 24
TITLE_H = 56


@dataclass
class MapOptions:
    detail: str = DEFAULT_DETAIL
    types: frozenset[str] | None = None
    show_subnets: bool = False
    include_unlinked: bool = False
    # Devices with no link to another device are left off unless asked for.
    hide_unlinked: bool = True
    site: str | None = None
    show_racks: bool = True
    layout: str = DEFAULT_LAYOUT

    @classmethod
    def from_params(cls, params: Any) -> "MapOptions":
        """Read options from query parameters or a submitted form.

        ``types`` repeats once per selected asset type; when it is absent every
        type is shown.
        """
        getlist = getattr(params, "getlist", None)
        raw_types = getlist("types") if getlist else params.get("types")
        if isinstance(raw_types, str):
            raw_types = [part for part in raw_types.split(",") if part]
        types = None
        if raw_types:
            types = frozenset(key for key in raw_types if key in asset_types.BY_KEY)
        detail = str(params.get("detail") or DEFAULT_DETAIL)
        site = str(params.get("site") or "").strip() or None
        layout = str(params.get("layout") or DEFAULT_LAYOUT)
        return cls(
            layout=layout if layout in LAYOUTS else DEFAULT_LAYOUT,
            detail=detail if detail in DETAIL_LEVELS else DEFAULT_DETAIL,
            types=types,
            show_subnets=_truthy(params.get("subnets")),
            include_unlinked=_truthy(params.get("all_devices")),
            hide_unlinked=_hide_unlinked(params),
            site=site,
            show_racks=str(params.get("racks") or "1") != "0",
        )

    def query(self) -> list[tuple[str, str]]:
        """Return these options as query parameters, for export links."""
        pairs = [("detail", self.detail)]
        if self.layout != DEFAULT_LAYOUT:
            pairs.append(("layout", self.layout))
        pairs += [("types", key) for key in sorted(self.types or ())]
        if self.show_subnets:
            pairs.append(("subnets", "1"))
        if self.include_unlinked:
            pairs.append(("all_devices", "1"))
        if not self.hide_unlinked:
            pairs.append(("unlinked", "show"))
        if self.site:
            pairs.append(("site", self.site))
        if not self.show_racks:
            pairs.append(("racks", "0"))
        return pairs


def _hide_unlinked(params: Any) -> bool:
    """Hiding is the default, so only an explicit ``unlinked=show`` turns it off.

    The option's checkbox (``unlinked=hide``) follows a hidden ``unlinked=show``
    field, so a ticked box submits both values and an unticked one only "show".
    """
    getlist = getattr(params, "getlist", None)
    values = getlist("unlinked") if getlist else [params.get("unlinked")] if params.get("unlinked") else []
    return not values or "hide" in values


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Node:
    id: str
    kind: str  # asset, item, network, internet
    label: str
    type_key: str
    site: str = UNPLACED_SITE
    rack_id: int | None = None
    rack_name: str | None = None
    start_unit: int = 0
    url: str | None = None
    ips: list[str] = field(default_factory=list)
    interfaces: list[dict[str, Any]] = field(default_factory=list)
    facts: list[tuple[str, str]] = field(default_factory=list)
    # A custom asset type's own name; catalogue types use their label.
    type_name: str | None = None
    # Layout
    x: float = 0.0
    y: float = 0.0
    h: float = 0.0

    @property
    def type(self) -> asset_types.AssetType:
        return asset_types.get(self.type_key)

    @property
    def type_label(self) -> str:
        return self.type_name or self.type.label

    @property
    def radio_count(self) -> int:
        return sum(1 for item in self.interfaces if item.get("kind") in links_repo.RADIO_KINDS)

    @property
    def anchor(self) -> tuple[float, float]:
        return self.x + CARD_W / 2, self.y + 8 + ICON / 2


@dataclass
class Edge:
    a: str
    b: str
    medium: str
    a_port: str | None = None
    b_port: str | None = None
    label: str | None = None
    details: list[str] = field(default_factory=list)
    count: int = 1
    # Links between two devices in the same rack column curve out beside the
    # rack; each gets its own lane so they do not sit on top of each other.
    lane: int | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        first, second = sorted((self.a, self.b))
        return first, second, self.medium


@dataclass
class Graph:
    nodes: dict[str, Node]
    edges: list[Edge]
    sites: list[str]
    racks: dict[int, dict[str, Any]]
    options: MapOptions


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def _frequency(mhz: Any) -> str | None:
    if mhz in (None, ""):
        return None
    value = int(mhz)
    return f"{value / 1000:g} GHz" if value >= 1000 else f"{value} MHz"


def _distance(metres: Any) -> str | None:
    if metres in (None, ""):
        return None
    value = int(metres)
    return f"{value / 1000:.1f} km" if value >= 1000 else f"{value} m"


def _speed(mbps: Any) -> str | None:
    if mbps in (None, ""):
        return None
    value = int(mbps)
    return f"{value / 1000:g} Gbps" if value >= 1000 else f"{value} Mbps"


def _radio_summary(interface: Mapping[str, Any]) -> str:
    parts = [
        links_repo.RADIO_MODES.get(str(interface.get("radio_mode") or ""), None),
        _frequency(interface.get("frequency_mhz")),
        f"{interface['channel_width_mhz']} MHz wide" if interface.get("channel_width_mhz") else None,
        f"SSID {interface['ssid']}" if interface.get("ssid") else None,
        f"{int(interface['azimuth_deg']):03d}°" if interface.get("azimuth_deg") is not None else None,
    ]
    return " · ".join(part for part in parts if part)


def _port_medium(label: str | None) -> str:
    text = (label or "").lower()
    if any(word in text for word in ("fibre", "fiber", "sfp", "lc", "om3", "om4", "os2")):
        return "fibre"
    if "dac" in text or "twinax" in text:
        return "dac"
    return "copper"


def build_graph(overview: Mapping[str, Any], extra: Mapping[str, Any],
                options: MapOptions | None = None) -> Graph:
    """Join racks, IPAM and assets into one graph of devices and links."""
    options = options or MapOptions()
    racks = {int(rack["id"]): dict(rack) for rack in overview.get("racks") or []}
    assets = {int(row["id"]): row for row in extra.get("assets") or [] if not row.get("archived_at")}
    nodes: dict[str, Node] = {}
    in_rack: set[str] = set()

    def asset_node(asset_id: int) -> Node | None:
        node_id = f"asset:{asset_id}"
        if node_id in nodes:
            return nodes[node_id]
        asset = assets.get(asset_id)
        if not asset:
            return None
        facts = []
        for label, key in (("Status", "status"), ("Serial", "serial_number"), ("OS", "os_name")):
            if asset.get(key):
                facts.append((label, str(asset[key])))
        node = Node(node_id, "asset", str(asset.get("name") or f"Asset {asset_id}"),
                    asset_types.effective(asset), url=f"/assets/{asset_id}",
                    site=str(asset.get("location") or "").strip() or UNPLACED_SITE, facts=facts,
                    type_name=asset_types.display_label(asset))
        nodes[node_id] = node
        return node

    # Racked equipment: the asset in the rack, or the rack item itself.
    port_owner: dict[int, tuple[str, str]] = {}
    for item in overview.get("equipment") or []:
        rack = racks.get(int(item["rack_id"])) or {}
        if item.get("asset_id") and int(item["asset_id"]) in assets:
            node = asset_node(int(item["asset_id"]))
        else:
            item_type = str(item.get("item_type") or "server")
            if item_type in RACK_ONLY_SKIPPED:
                continue
            node = Node(f"item:{item['id']}", "item",
                        str(item.get("name") or item.get("asset_name") or "Rack item"),
                        rack_item_types.get(item_type).asset_type,
                        url=f"/racks?rack={item['rack_id']}#placement-{item['id']}")
            nodes[node.id] = node
        assert node is not None
        node.rack_id = int(item["rack_id"])
        node.rack_name = str(rack.get("name") or item.get("rack_name") or "Rack")
        node.site = str(rack.get("location") or "").strip() or node.site
        node.start_unit = int(item.get("start_unit") or 0)
        in_rack.add(node.id)
        for port in item.get("ports") or []:
            if str(port.get("connector") or "data") == "data":
                port_owner[int(port["id"])] = (node.id, str(port.get("display_label") or "Port"))

    edges: dict[tuple[str, str, str], Edge] = {}

    def add_edge(edge: Edge) -> None:
        if edge.a == edge.b or edge.a not in nodes or edge.b not in nodes:
            return
        existing = edges.get(edge.key)
        if existing:
            existing.count += 1
            # Keep the first link's labels; note that there are more.
            return
        edges[edge.key] = edge

    # Rack patching: port-to-port peers (stored on both ends) and port-to-asset links.
    seen_peers: set[tuple[int, int]] = set()
    for item in overview.get("equipment") or []:
        for port in item.get("ports") or []:
            port_id = int(port["id"])
            owner = port_owner.get(port_id)
            if not owner:
                continue
            peer_id = port.get("peer_port_id")
            if peer_id and int(peer_id) in port_owner:
                pair = tuple(sorted((port_id, int(peer_id))))
                if pair not in seen_peers:
                    seen_peers.add(pair)  # type: ignore[arg-type]
                    far = port_owner[int(peer_id)]
                    add_edge(Edge(owner[0], far[0], _port_medium(port.get("label")),
                                  a_port=owner[1], b_port=far[1], label=port.get("label")))
            elif port.get("asset_id") and int(port["asset_id"]) in assets:
                target = asset_node(int(port["asset_id"]))
                if target:
                    add_edge(Edge(owner[0], target.id, _port_medium(port.get("label")),
                                  a_port=owner[1], label=port.get("label")))

    # Interfaces on assets, including radios and WAN uplinks.
    interfaces = {int(row["id"]): row for row in extra.get("interfaces") or []}
    for interface in interfaces.values():
        node = asset_node(int(interface["asset_id"]))
        if not node:
            continue
        entry = {"name": interface["name"], "kind": interface["kind"],
                 "ip": interface.get("ip_address"), "mac": interface.get("mac_address"),
                 "speed": _speed(interface.get("speed_mbps")), "vlan": interface.get("vlan"),
                 "radio": _radio_summary(interface) if interface["kind"] in links_repo.RADIO_KINDS else None}
        node.interfaces.append(entry)
        if interface.get("ip_address") and interface["ip_address"] not in node.ips:
            node.ips.append(str(interface["ip_address"]))
        if interface["kind"] == "wan":
            if INTERNET_ID not in nodes:
                nodes[INTERNET_ID] = Node(INTERNET_ID, "internet", "Internet", "cloud_service")
            add_edge(Edge(node.id, INTERNET_ID, "wan", a_port=str(interface["name"]),
                          label=interface.get("ip_address")))

    def endpoint(kind: str, record_id: int) -> tuple[str, str] | None:
        if kind == "interface":
            interface = interfaces.get(record_id)
            if not interface:
                return None
            return f"asset:{interface['asset_id']}", str(interface["name"])
        return port_owner.get(record_id)

    for link in extra.get("links") or []:
        a = endpoint(str(link["a_kind"]), int(link["a_id"]))
        b = endpoint(str(link["b_kind"]), int(link["b_id"]))
        if not a or not b:
            continue  # An end was removed; the link no longer draws.
        medium = str(link.get("medium") or "copper")
        details = [part for part in (
            _frequency(link.get("frequency_mhz")), _distance(link.get("distance_m")),
            f"{link['signal_dbm']} dBm" if link.get("signal_dbm") is not None else None,
            _speed(link.get("speed_mbps"))) if part]
        add_edge(Edge(a[0], b[0], medium if medium in MEDIUM_STYLES else "other",
                      a_port=a[1], b_port=b[1], label=link.get("label"), details=details))

    # IPAM: each asset's addresses, and optionally its subnets as nodes.
    networks = {int(row["id"]): row for row in overview.get("networks") or []}
    for address in overview.get("addresses") or []:
        if not address.get("asset_id") or int(address["asset_id"]) not in assets:
            continue
        node = asset_node(int(address["asset_id"]))
        if not node:
            continue
        if address["address"] not in node.ips:
            node.ips.append(str(address["address"]))
        network = networks.get(int(address["network_id"]))
        if options.show_subnets and network:
            network_id = f"network:{network['id']}"
            if network_id not in nodes:
                nodes[network_id] = Node(network_id, "network", str(network["name"]), "cloud_service",
                                         url=f"/ipam#network-{network['id']}",
                                         facts=[("CIDR", str(network["cidr"]))])
            add_edge(Edge(node.id, network_id, "subnet"))

    # Documented "connected to" relationships fill gaps where no port-level link exists.
    linked_pairs = {edge.key[:2] for edge in edges.values()}
    for relationship in extra.get("relationships") or []:
        source = asset_node(int(relationship["source_asset_id"]))
        target = asset_node(int(relationship["target_id"]))
        if source and target and tuple(sorted((source.id, target.id))) not in linked_pairs:
            add_edge(Edge(source.id, target.id, "relationship"))

    # Every remaining asset is a candidate; infrastructure is always drawn.
    for asset_id in assets:
        asset_node(asset_id)

    edge_list = list(edges.values())
    connected = {end for edge in edge_list for end in (edge.a, edge.b)}

    def keep(node: Node) -> bool:
        if node.kind in {"network", "internet"}:
            return True
        if options.types is not None and node.type_key not in options.types:
            return False
        if node.kind == "item":
            return True
        return (options.include_unlinked or node.id in connected or node.id in in_rack
                or bool(node.ips) or bool(node.interfaces)
                or node.type.category in INFRASTRUCTURE_CATEGORIES)

    nodes = {node_id: node for node_id, node in nodes.items() if keep(node)}
    edge_list = [edge for edge in edge_list if edge.a in nodes and edge.b in nodes]

    if options.site:
        focus = {node_id for node_id, node in nodes.items() if node.site == options.site}
        neighbours = {end for edge in edge_list if edge.a in focus or edge.b in focus
                      for end in (edge.a, edge.b)}
        nodes = {node_id: node for node_id, node in nodes.items() if node_id in focus | neighbours}
        edge_list = [edge for edge in edge_list if edge.a in nodes and edge.b in nodes]

    if options.hide_unlinked:
        # A subnet is not a device, so membership alone does not count as a link.
        device_links = {end for edge in edge_list if edge.medium != "subnet" for end in (edge.a, edge.b)}
        nodes = {node_id: node for node_id, node in nodes.items()
                 if node.kind not in {"asset", "item"} or node_id in device_links}
        edge_list = [edge for edge in edge_list if edge.a in nodes and edge.b in nodes]

    # Subnets and the internet only draw while something still links to them.
    linked = {end for edge in edge_list for end in (edge.a, edge.b)}
    nodes = {node_id: node for node_id, node in nodes.items()
             if node.kind not in {"network", "internet"} or node_id in linked}
    edge_list = [edge for edge in edge_list if edge.a in nodes and edge.b in nodes]

    if not options.show_racks:
        for node in nodes.values():
            node.rack_id = None
    sites = sorted({node.site for node in nodes.values() if node.kind in {"asset", "item"}},
                   key=lambda name: (name == UNPLACED_SITE, name.casefold()))
    return Graph(nodes, edge_list, sites, racks, options)


def site_names(overview: Mapping[str, Any], extra: Mapping[str, Any]) -> list[str]:
    """Every site a map could be focused on, for the site filter."""
    names = {str(rack.get("location") or "").strip() for rack in overview.get("racks") or []}
    names |= {str(asset.get("location") or "").strip() for asset in extra.get("assets") or []
              if not asset.get("archived_at")}
    return sorted((name for name in names if name), key=str.casefold)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _text_lines(node: Node, detail: str) -> list[tuple[str, str]]:
    """Lines drawn under a node's icon as (css class, text)."""
    lines: list[tuple[str, str]] = [("nm-name", node.label)]
    if node.kind == "network":
        lines += [("nm-meta", value) for _label, value in node.facts]
        return lines
    if detail == "overview":
        return lines
    if node.kind != "internet":
        lines.append(("nm-meta", node.type_label))
    ips = node.ips if detail == "detailed" else node.ips[:2]
    lines += [("nm-ip", ip) for ip in ips]
    if detail == "standard" and len(node.ips) > 2:
        lines.append(("nm-meta", f"+{len(node.ips) - 2} more addresses"))
    if detail == "standard" and node.radio_count:
        radios = [item for item in node.interfaces if item["kind"] in links_repo.RADIO_KINDS]
        for radio in radios[:2]:
            lines.append(("nm-radio", f"{radio['name']}: {radio['radio'] or 'radio'}"))
    if detail == "detailed":
        for interface in node.interfaces:
            if interface["kind"] in links_repo.RADIO_KINDS:
                lines.append(("nm-radio", f"{interface['name']}: {interface['radio'] or 'radio'}"))
            else:
                parts = [interface["name"], interface.get("ip"), interface.get("speed"),
                         f"VLAN {interface['vlan']}" if interface.get("vlan") else None]
                lines.append(("nm-if", " · ".join(str(part) for part in parts if part)))
        for label, value in node.facts:
            if label != "Status":
                lines.append(("nm-meta", f"{label}: {value}"))
    return lines


def _node_height(node: Node, detail: str) -> float:
    return 8 + ICON + 6 + LINE_H * len(_text_lines(node, detail)) + 8


@dataclass
class Box:
    kind: str  # site or rack
    label: str
    x: float
    y: float
    w: float
    h: float
    sublabel: str | None = None


def _neighbours(edges: Iterable[Edge]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for edge in edges:
        result.setdefault(edge.a, set()).add(edge.b)
        result.setdefault(edge.b, set()).add(edge.a)
    return result


def _place_rows(nodes: list[Node], left: float, top: float, detail: str,
                neighbours: dict[str, set[str]], placed: dict[str, Node],
                per_row: int) -> tuple[float, float]:
    """Place loose nodes in rows by tier, ordering each row towards its links.

    Returns the width and height used.
    """
    width = 0.0
    y = top
    tiers: dict[int, list[Node]] = {}
    for node in nodes:
        tiers.setdefault(node.type.tier, []).append(node)
    for tier in sorted(tiers):
        members = sorted(tiers[tier], key=lambda node: node.label.casefold())

        def barycentre(node: Node) -> float:
            xs = [placed[other].x for other in neighbours.get(node.id, ()) if other in placed]
            return sum(xs) / len(xs) if xs else math.inf

        members.sort(key=lambda node: (barycentre(node), node.label.casefold()))
        for start in range(0, len(members), per_row):
            row = members[start:start + per_row]
            row_height = max(_node_height(node, detail) for node in row)
            for index, node in enumerate(row):
                node.x = left + index * CELL_W
                node.y = y
                node.h = _node_height(node, detail)
                placed[node.id] = node
            width = max(width, len(row) * CELL_W)
            y += row_height + GAP
    return width, max(0.0, y - top - GAP)


def layout(graph: Graph) -> tuple[float, float, list[Box]]:
    """Position every node, returning the drawing size and any site/rack boxes."""
    if graph.options.layout == "topology":
        return _layout_topology(graph)
    return _layout_sites(graph)


# Topology layout metrics: columns per hop from the internet edge.
COLUMN_W = {"overview": 210, "standard": 250, "detailed": 270}
ROW_GAP = 22
COMPONENT_GAP = 48


def _root_rank(node: Node, degree: int) -> tuple[Any, ...]:
    """Order candidate roots: the internet, then edge devices, then the busiest."""
    return (node.kind != "internet", node.type.tier, -degree, node.label.casefold())


def spanning_forest(graph: Graph) -> tuple[list[str], dict[str, list[str]]]:
    """Return tree roots and each node's children, breadth-first from the edge.

    Every connected group of devices becomes one tree rooted at the internet
    (or, without one, its modem/router/firewall), so each device sits one hop
    to the right of the device it connects through. Subnet membership is not
    a physical link and does not shape the tree.
    """
    physical = [edge for edge in graph.edges if edge.medium != "subnet"
                and graph.nodes[edge.a].kind != "network" and graph.nodes[edge.b].kind != "network"]
    neighbours = _neighbours(physical)
    members = [node for node in graph.nodes.values() if node.kind != "network"]

    def child_order(node_id: str) -> tuple[Any, ...]:
        node = graph.nodes[node_id]
        # Uplinks first, then larger subtrees, so the tree reads top-down like a rack.
        return (node.type.tier, -len(neighbours.get(node_id, ())), node.label.casefold())

    roots: list[str] = []
    children: dict[str, list[str]] = {}
    seen: set[str] = set()
    remaining = sorted(members, key=lambda node: _root_rank(node, len(neighbours.get(node.id, ()))))
    for candidate in remaining:
        if candidate.id in seen:
            continue
        roots.append(candidate.id)
        seen.add(candidate.id)
        queue = [candidate.id]
        while queue:
            current = queue.pop(0)
            for other in sorted(neighbours.get(current, ()), key=child_order):
                if other not in seen:
                    seen.add(other)
                    children.setdefault(current, []).append(other)
                    queue.append(other)
    return roots, children


def _layout_topology(graph: Graph) -> tuple[float, float, list[Box]]:
    detail = graph.options.detail
    column = COLUMN_W.get(detail, COLUMN_W["standard"])
    for node in graph.nodes.values():
        node.h = _node_height(node, detail)
    roots, children = spanning_forest(graph)
    connected = [root for root in roots if children.get(root)]
    isolated = [root for root in roots if not children.get(root)]

    # Subnets (when shown) run along the top, like the site layout's band.
    top = MARGIN + TITLE_H
    band = sorted((node for node in graph.nodes.values() if node.kind == "network"),
                  key=lambda node: node.label.casefold())
    for index, node in enumerate(band):
        node.x, node.y = MARGIN + index * CELL_W, top
    if band:
        top += max(node.h for node in band) + GAP * 2

    cursor = top
    width = float(MARGIN + len(band) * CELL_W)

    def place(node_id: str, depth: int) -> None:
        # Leaves take the next free row; a parent is centred on its children,
        # so every link runs rightwards from its uplink.
        nonlocal cursor, width
        node = graph.nodes[node_id]
        node.x = MARGIN + depth * column
        kids = children.get(node_id, [])
        if not kids:
            node.y = cursor
            cursor += node.h + ROW_GAP
        else:
            for kid in kids:
                place(kid, depth + 1)
            first, last = graph.nodes[kids[0]], graph.nodes[kids[-1]]
            node.y = (first.y + last.y) / 2
            cursor = max(cursor, node.y + node.h + ROW_GAP)
        width = max(width, node.x + CARD_W + MARGIN)

    for root in connected:
        place(root, 0)
        cursor += COMPONENT_GAP - ROW_GAP

    # Devices with no documented links sit in a grid underneath.
    boxes: list[Box] = []
    if isolated:
        per_row = max(4, int((max(width, 1100) - 2 * MARGIN) // CELL_W))
        grid_top = cursor + (SITE_HEADER if connected else 0)
        row_height = 0.0
        for index, node_id in enumerate(isolated):
            node = graph.nodes[node_id]
            row, col = divmod(index, per_row)
            if col == 0 and row:
                grid_top += row_height + ROW_GAP
                row_height = 0.0
            node.x, node.y = MARGIN + col * CELL_W, grid_top
            row_height = max(row_height, node.h)
            width = max(width, node.x + CARD_W + MARGIN)
        if connected:
            boxes.append(Box("heading", "Not linked to other devices", MARGIN, cursor, 0, 0))
        cursor = grid_top + row_height + ROW_GAP
    return max(width, 640.0), cursor - ROW_GAP + MARGIN, boxes


def _layout_sites(graph: Graph) -> tuple[float, float, list[Box]]:
    """Group devices into boxes by site, and by rack within a site."""
    detail = graph.options.detail
    neighbours = _neighbours(graph.edges)
    placed: dict[str, Node] = {}
    boxes: list[Box] = []

    # Top band: the internet and subnets sit above the sites they serve.
    band = [node for node in graph.nodes.values() if node.kind in {"internet", "network"}]
    band.sort(key=lambda node: (node.kind != "internet", node.label.casefold()))
    y = MARGIN + TITLE_H
    band_height = 0.0
    for index, node in enumerate(band):
        node.x = MARGIN + index * CELL_W
        node.y = y
        node.h = _node_height(node, detail)
        placed[node.id] = node
        band_height = max(band_height, node.h)
    top = y + (band_height + GAP * 2 if band else 0)

    # Sites, packed left to right and wrapped into rows.
    x = MARGIN
    row_top = top
    row_height = 0.0
    total_width = float(MARGIN)
    for site in graph.sites:
        members = [node for node in graph.nodes.values()
                   if node.site == site and node.kind in {"asset", "item"}]
        racked: dict[int, list[Node]] = {}
        loose: list[Node] = []
        for node in members:
            if node.rack_id is not None:
                racked.setdefault(node.rack_id, []).append(node)
            else:
                loose.append(node)
        inner_left = SITE_PAD
        inner_top = SITE_HEADER
        rack_layouts = []
        for rack_id in sorted(racked, key=lambda rid: str(graph.racks.get(rid, {}).get("name") or "").casefold()):
            column = sorted(racked[rack_id], key=lambda node: (-node.start_unit, node.label.casefold()))
            height = RACK_HEADER + sum(_node_height(node, detail) + 8 for node in column) + 4
            order = {node.id: index for index, node in enumerate(column)}
            internal = [edge for edge in graph.edges if edge.a in order and edge.b in order]
            internal.sort(key=lambda edge: abs(order[edge.a] - order[edge.b]))
            for lane, edge in enumerate(internal):
                edge.lane = lane
            lanes_width = len(internal) * LANE_W + 24 if internal else 0
            rack_layouts.append((rack_id, column, height, CELL_W + 4 + lanes_width))
        racks_width = sum(layout[3] + 16 for layout in rack_layouts)
        per_row = max(3, min(7, math.ceil(math.sqrt(max(1, len(loose))) * 1.4)))
        loose_left_offset = inner_left + racks_width + (GAP if rack_layouts and loose else 0)

        # Place racks first, then loose devices beside them.
        first_rack_box = len(boxes)
        rack_heights = 0.0
        rack_x = x + inner_left
        for rack_id, column, height, rack_width in rack_layouts:
            rack_y = row_top + inner_top
            rack = graph.racks.get(rack_id, {})
            boxes.append(Box("rack", str(rack.get("name") or "Rack"), rack_x, rack_y, rack_width, height,
                             f"{rack['unit_count']}U" if rack.get("unit_count") else None))
            cursor = rack_y + RACK_HEADER
            for node in column:
                node.x = rack_x + 12
                node.y = cursor
                node.h = _node_height(node, detail)
                placed[node.id] = node
                cursor += node.h + 8
            rack_heights = max(rack_heights, height)
            rack_x += rack_width + 16
        loose_width, loose_height = _place_rows(
            loose, x + loose_left_offset, row_top + inner_top, detail, neighbours, placed, per_row)
        content_width = max(loose_left_offset - inner_left + loose_width, CELL_W)
        site_width = inner_left + content_width + SITE_PAD
        site_height = inner_top + max(rack_heights, loose_height, 60) + SITE_PAD
        if x > MARGIN and x + site_width > MAX_ROW_WIDTH:
            # Wrap: move this site to the start of a new row.
            dx = MARGIN - x
            dy = row_height + GAP * 2
            for node in members:
                node.x += dx
                node.y += dy
            for box in boxes[first_rack_box:]:
                box.x += dx
                box.y += dy
            x = MARGIN
            row_top += dy
            row_height = 0.0
        boxes.append(Box("site", site, x, row_top, site_width, site_height,
                         f"{len(members)} device{'s' if len(members) != 1 else ''}"))
        x += site_width + GAP * 2
        row_height = max(row_height, site_height)
        total_width = max(total_width, x - GAP * 2 + MARGIN)
    # Centre the internet and subnets over the devices they connect to,
    # keeping them in order and apart.
    desired = []
    for node in band:
        xs = [placed[other].x for other in neighbours.get(node.id, ()) if other in placed and other not in {n.id for n in band}]
        desired.append((sum(xs) / len(xs) if xs else node.x, node))
    cursor = float(MARGIN)
    for want, node in sorted(desired, key=lambda item: item[0]):
        node.x = max(cursor, want)
        cursor = node.x + CELL_W
    total_width = max(total_width, cursor + MARGIN)
    total_height = row_top + row_height + MARGIN
    return max(total_width, 640.0), total_height, boxes


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

def _attr(value: Any) -> str:
    return escape(str(value), {'"': "&quot;"})


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', monospace"

# Styling is written as presentation attributes rather than CSS so browsers,
# the browser's canvas (PNG export) and WeasyPrint (PDF export) all draw the
# same thing; WeasyPrint ignores the CSS ``font`` shorthand and ``paint-order``.
PRESENTATION: dict[str, dict[str, Any]] = {
    "nm-bg": {"fill": "#ffffff"},
    "nm-title": {"font-family": SANS, "font-size": 18, "font-weight": 600, "fill": "#0f172a"},
    "nm-subtitle": {"font-family": SANS, "font-size": 12, "fill": "#475569"},
    "nm-site": {"fill": "#f8fafc", "stroke": "#cbd5e1", "stroke-width": 1.2},
    "nm-site-label": {"font-family": SANS, "font-size": 14, "font-weight": 600, "fill": "#0f172a"},
    "nm-rack": {"fill": "#eef2f7", "stroke": "#94a3b8", "stroke-width": 1, "stroke-dasharray": "4 3"},
    "nm-rack-label": {"font-family": SANS, "font-size": 11, "font-weight": 600, "fill": "#334155"},
    "nm-box-meta": {"font-family": SANS, "font-size": 11, "fill": "#64748b"},
    "nm-card": {"fill": "#ffffff", "stroke-width": 1},
    "nm-name": {"font-family": SANS, "font-size": 11.5, "font-weight": 600, "fill": "#0f172a"},
    "nm-meta": {"font-family": SANS, "font-size": 10, "fill": "#64748b"},
    "nm-ip": {"font-family": MONO, "font-size": 10, "fill": "#1e293b"},
    "nm-if": {"font-family": MONO, "font-size": 9.5, "fill": "#334155"},
    "nm-radio": {"font-family": SANS, "font-size": 9.5, "fill": "#1d4ed8"},
    "nm-edge-label": {"font-family": SANS, "font-size": 10, "fill": "#1e293b"},
    "nm-edge-label-radio": {"font-family": SANS, "font-size": 10, "font-weight": 600, "fill": "#1d4ed8"},
    "nm-port": {"font-family": SANS, "font-size": 9, "fill": "#475569"},
    "nm-legend": {"font-family": SANS, "font-size": 11, "fill": "#334155"},
    "nm-legend-title": {"font-family": SANS, "font-size": 12, "font-weight": 600, "fill": "#0f172a"},
}


def _presentation(css: str) -> str:
    return " ".join(f'{key}="{_attr(value)}"' for key, value in PRESENTATION.get(css, {}).items())


def _apply_presentation(svg: str) -> str:
    return re.sub(r'class="(nm-[a-z-]+)"',
                  lambda match: f'class="{match.group(1)}" {_presentation(match.group(1))}'.rstrip(), svg)


def _label(css: str, x: float, y: float, text: str, anchor: str = "middle") -> str:
    """Text with a white halo drawn underneath, so it reads over lines."""
    common = f'x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}"'
    font = " ".join(f'{key}="{_attr(value)}"' for key, value in PRESENTATION[css].items() if key != "fill")
    return (f'<text class="nm-halo" {common} {font} fill="#ffffff" stroke="#ffffff" stroke-width="3" '
            f'stroke-linejoin="round" aria-hidden="true">{text}</text>'
            f'<text class="{css}" {common}>{text}</text>')


# A small antenna-with-waves badge marking devices that carry radio links.
RADIO_BADGE = (
    '<symbol id="nm-radio-badge" viewBox="0 0 20 20">'
    '<circle cx="10" cy="10" r="9.5" fill="#2563eb" stroke="#fff" stroke-width="1"/>'
    '<path d="M10 8v7M8 15h4" stroke="#fff" stroke-width="1.6" stroke-linecap="round" fill="none"/>'
    '<path d="M6.5 7.5a4.5 4.5 0 0 1 7 0M4.5 5.5a7.5 7.5 0 0 1 11 0" stroke="#fff" stroke-width="1.3" '
    'stroke-linecap="round" fill="none"/></symbol>'
)


def _edge_path(a: Node, b: Node, bend: float, lane: int | None = None) -> tuple[str, tuple[float, float], tuple[float, float], tuple[float, float]]:
    """Return an SVG path between two nodes, its midpoint and points near each end."""
    (x1, y1), (x2, y2) = a.anchor, b.anchor
    if abs(x1 - x2) < 1:
        # Same column (e.g. one rack): bow out to the side so the line does
        # not run underneath the devices between them.
        offset = CARD_W / 2 + 16 + (lane or 0) * LANE_W + bend
        cx = x1 + offset
        path = f"M{x1:.1f},{y1:.1f} C{cx:.1f},{y1:.1f} {cx:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"
        mid = (x1 + offset * 0.75, (y1 + y2) / 2)
        near_a = (x1 + CARD_W / 2 + 6, y1 + (6 if y2 > y1 else -6))
        near_b = (x2 + CARD_W / 2 + 6, y2 + (-6 if y2 > y1 else 6))
        return path, mid, near_a, near_b
    if bend:
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy) or 1
        cx = (x1 + x2) / 2 - dy / length * bend
        cy = (y1 + y2) / 2 + dx / length * bend
        path = f"M{x1:.1f},{y1:.1f} Q{cx:.1f},{cy:.1f} {x2:.1f},{y2:.1f}"
        mid = ((x1 + 2 * cx + x2) / 4, (y1 + 2 * cy + y2) / 4)
    else:
        path = f"M{x1:.1f},{y1:.1f} L{x2:.1f},{y2:.1f}"
        mid = ((x1 + x2) / 2, (y1 + y2) / 2)

    def along(t: float, start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
        return start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t

    # Port labels sit just outside each card along the line.
    length = math.hypot(x2 - x1, y2 - y1) or 1
    t = min(0.45, (CARD_W / 2 + 14) / length)
    return path, mid, along(t, (x1, y1), (x2, y2)), along(t, (x2, y2), (x1, y1))


def _bezier(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    u = 1 - t
    return tuple(u ** 3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t ** 3 * d  # type: ignore[return-value]
                 for a, b, c, d in zip(p0, p1, p2, p3))


def _topology_path(a: Node, b: Node, bend: float) -> tuple[str, tuple[float, float],
                                                             tuple[float, float, str], tuple[float, float, str]]:
    """A smooth horizontal curve between two devices, as in a controller's topology view.

    Returns the path, its midpoint and a (x, y, text-anchor) spot for each
    end's port label.
    """
    (ax, ay), (bx, by) = a.anchor, b.anchor
    reach = ICON / 2 + 4
    if abs(ax - bx) < 1:
        # Devices in the same column: loop out to the right of both.
        x1, x2 = ax + reach, bx + reach
        offset = 36 + min(150.0, abs(by - ay) * 0.3) + bend
        path = f"M{x1:.1f},{ay:.1f} C{x1 + offset:.1f},{ay:.1f} {x2 + offset:.1f},{by:.1f} {x2:.1f},{by:.1f}"
        return (path, (x1 + offset * 0.75, (ay + by) / 2),
                (x1 + 4, ay - 6, "start"), (x2 + 4, by - 6, "start"))
    flip = ax > bx
    (lx, ly), (rx, ry) = ((bx, by), (ax, ay)) if flip else ((ax, ay), (bx, by))
    x1, x2 = lx + reach, rx - reach
    pull = (x2 - x1) * 0.5
    p0, p1, p2, p3 = (x1, ly), (x1 + pull, ly + bend), (x2 - pull, ry + bend), (x2, ry)
    path = (f"M{x1:.1f},{ly:.1f} C{p1[0]:.1f},{p1[1]:.1f} {p2[0]:.1f},{p2[1]:.1f} {x2:.1f},{ry:.1f}")
    mid = _bezier(p0, p1, p2, p3, 0.5)
    # The uplink end's label sits a little way along the curve, where links
    # fanning out from one port have already separated; the far end's label
    # sits just before the device it reaches.
    near_left = _bezier(p0, p1, p2, p3, 0.32)
    left_label = (near_left[0], near_left[1] - 5, "middle")
    right_label = (x2 - 6, ry - 6, "end")
    return (path, mid, right_label, left_label) if flip else (path, mid, left_label, right_label)


def _topology_fan_x(a: Node, b: Node, bend: float, t: float) -> float:
    """How far past the uplink icon the curve is at ``t`` (a 0.3-ish label sits
    a little way along the link). With several links leaving the same device
    each one is nudged a different distance out so its port label fans away
    instead of every label piling up at the icon's edge.

    ``a`` and ``b`` are the two endpoints (in any order); the curve leaving the
    left/uplink end is used, and the returned offset is always >= 0.
    """
    (ax, _ay), (bx, _by) = a.anchor, b.anchor
    reach = ICON / 2 + 4
    if abs(ax - bx) < 1:
        return 0.0
    (lx, ly), (rx, ry) = ((bx, _by), (ax, _ay)) if ax > bx else ((ax, _ay), (bx, _by))
    x1, x2 = lx + reach, rx - reach
    pull = (x2 - x1) * 0.5
    p0, p1, p2, p3 = (x1, ly), (x1 + pull, ly + bend), (x2 - pull, ry + bend), (x2, ry)
    return max(0.0, _bezier(p0, p1, p2, p3, t)[0] - x1)


def _link_label_parts(edge: Edge) -> list[str]:
    """The textual parts of a link's label, in display order.

    Wireless links lead with their frequency, distance and signal; every other
    medium leads with the label. A count of bundled links is appended last.
    """
    ordered = (*edge.details, edge.label) if edge.medium == "wireless" else (edge.label, *edge.details)
    parts = [part for part in ordered if part]
    if edge.count > 1:
        parts.append(f"×{edge.count}")
    return parts


def render_svg(graph: Graph, *, title: str, subtitle: str | None = None,
               interactive: bool = False) -> str:
    """Draw the graph as a standalone SVG document."""
    width, height, boxes = layout(graph)
    detail = graph.options.detail
    legend_types = sorted({node.type_key for node in graph.nodes.values() if node.kind in {"asset", "item"}},
                          key=lambda key: (asset_types.get(key).tier, asset_types.get(key).label))
    legend_media = [key for key in MEDIUM_STYLES if any(edge.medium == key for edge in graph.edges)]
    legend_rows = math.ceil(len(legend_types) / 5) + (1 if legend_media else 0)
    legend_height = 34 + legend_rows * 24 if (legend_types or legend_media) else 0
    total_height = height + legend_height + (12 if legend_height else 0)
    used_icons = {node.type_key for node in graph.nodes.values()}

    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {width:.0f} {total_height:.0f}" width="{width:.0f}" height="{total_height:.0f}" '
        f'class="network-map-svg" role="img" aria-label="{_attr(title)}">',
        f"<title>{escape(title)}</title><defs>{RADIO_BADGE}",
    ]
    for key in sorted(used_icons):
        out.append(f'<symbol id="nm-icon-{key}" viewBox="0 0 48 48">{asset_types.icon_markup(key)}</symbol>')
    out.append("</defs>")
    out.append(f'<rect class="nm-bg" x="0" y="0" width="{width:.0f}" height="{total_height:.0f}"/>')
    out.append(f'<text class="nm-title" x="{MARGIN}" y="{MARGIN + 16}">{escape(title)}</text>')
    if subtitle:
        out.append(f'<text class="nm-subtitle" x="{MARGIN}" y="{MARGIN + 36}">{escape(subtitle)}</text>')

    topology = graph.options.layout == "topology"
    for box in [box for box in boxes if box.kind != "rack"] + [box for box in boxes if box.kind == "rack"]:
        if box.kind == "heading":
            out.append(f'<text class="nm-site-label" x="{box.x:.1f}" y="{box.y + 20:.1f}">{escape(box.label)}</text>')
        elif box.kind == "site":
            out.append(f'<g class="nm-site-box" data-site="{_attr(box.label)}"><rect class="nm-site" x="{box.x:.1f}" y="{box.y:.1f}" '
                       f'width="{box.w:.1f}" height="{box.h:.1f}" rx="10"/>'
                       f'<text class="nm-site-label" x="{box.x + SITE_PAD:.1f}" y="{box.y + 22:.1f}">{escape(_clip(box.label, 60))}</text>'
                       f'<text class="nm-box-meta" x="{box.x + box.w - SITE_PAD:.1f}" y="{box.y + 22:.1f}" text-anchor="end">{escape(box.sublabel or "")}</text></g>')
        else:
            out.append(f'<g class="nm-rack-box"><rect class="nm-rack" x="{box.x:.1f}" y="{box.y:.1f}" width="{box.w:.1f}" '
                       f'height="{box.h:.1f}" rx="6"/><text class="nm-rack-label" x="{box.x + 8:.1f}" y="{box.y + 16:.1f}">'
                       f'{escape(_clip(box.label, 18))}</text><text class="nm-box-meta" x="{box.x + box.w - 8:.1f}" '
                       f'y="{box.y + 16:.1f}" text-anchor="end">{escape(box.sublabel or "")}</text></g>')

    # Edges under the device cards; labels are drawn after the cards so they stay readable.
    labels: list[str] = []
    side_slots: dict[str, int] = {}
    topo_slots: dict[tuple[str, int], tuple[int, int]] = {}
    pair_counts: dict[tuple[str, str], int] = {}
    # In the topology view a device can have several ports (and their link
    # labels) leaving the same side. Order each side by the connected devices'
    # drawn positions, rather than link-record order, so labels follow their
    # branches. Reserve annotation lines and centre the block on the icon.
    topo_groups: dict[tuple[str, str], list[tuple[float, int, int]]] = {}
    if topology:
        for index, edge in enumerate(graph.edges):
            if detail == "overview" or edge.medium == "subnet":
                continue
            a, b = graph.nodes[edge.a], graph.nodes[edge.b]
            a_labelled = edge.medium != "wireless" and bool(_link_label_parts(edge))
            for node, peer, is_a, port in ((a, b, True, edge.a_port), (b, a, False, edge.b_port)):
                if not port and not (is_a and a_labelled):
                    continue
                if abs(node.anchor[0] - peer.anchor[0]) < 1 or node.anchor[0] < peer.anchor[0]:
                    side = "right"
                else:
                    side = "left"
                lines = (1 if port else 0) + (1 if (is_a and a_labelled) else 0)
                topo_groups.setdefault((node.id, side), []).append((peer.anchor[1], index, lines))
        for (node_id, _side), entries in topo_groups.items():
            total = sum(lines for _peer_y, _index, lines in entries)
            slot = 0
            for _peer_y, index, lines in sorted(entries):
                topo_slots[(node_id, index)] = (slot, total)
                slot += lines
        # When several links leave the same device, spread their uplink-side
        # port labels along the curves so they fan out instead of stacking
        # on one point. Each uplink end gets a fan position; the main loop
        # turns it into a distance out along that link's curve.
        fan_t: dict[tuple[str, str], float] = {}
        fan_groups: dict[str, list[tuple[str, str, float]]] = {}
        for edge in graph.edges:
            if detail == "overview" or edge.medium in ("subnet", "wireless"):
                continue
            a, b = graph.nodes[edge.a], graph.nodes[edge.b]
            uplink = edge.a if a.anchor[0] <= b.anchor[0] else edge.b
            other = edge.b if uplink == edge.a else edge.a
            fan_groups.setdefault(uplink, []).append((edge.a, edge.b, graph.nodes[other].anchor[1]))
        for uplink, group in fan_groups.items():
            if len(group) < 2:
                continue
            ordered = sorted(group, key=lambda item: item[2])
            for pos, (ea, eb, _y) in enumerate(ordered):
                fan_t[(ea, eb)] = max(0.18, min(0.7, 0.42 + (pos - (len(ordered) - 1) / 2) * 0.13))
    for index, edge in enumerate(graph.edges):
        a, b = graph.nodes[edge.a], graph.nodes[edge.b]
        pair = tuple(sorted((edge.a, edge.b)))
        seen = pair_counts.get(pair, 0)  # type: ignore[arg-type]
        pair_counts[pair] = seen + 1  # type: ignore[index]
        bend = 0.0 if seen == 0 else (18.0 * ((seen + 1) // 2) * (1 if seen % 2 else -1))
        if topology:
            path, mid, spot_a, spot_b = _topology_path(a, b, bend)
        else:
            path, mid, near_a, near_b = _edge_path(a, b, bend, edge.lane)
            spot_a, spot_b = (*near_a, "middle"), (*near_b, "middle")
        style = MEDIUM_STYLES.get(edge.medium, MEDIUM_STYLES["other"])
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        width_px = style["width"] + (1.2 if edge.count > 1 else 0)
        title_parts = [f"{a.label}{' · ' + edge.a_port if edge.a_port else ''}",
                       f"{b.label}{' · ' + edge.b_port if edge.b_port else ''}"]
        out.append(f'<g class="nm-edge" data-edge="{index}" data-medium="{edge.medium}" '
                   f'data-a="{_attr(edge.a)}" data-b="{_attr(edge.b)}"><title>'
                   f'{escape(" ↔ ".join(title_parts))} ({escape(style["label"])})</title>'
                   f'<path d="{path}" fill="none" stroke="{style["colour"]}" stroke-width="{width_px:.1f}"'
                   f'{dash} stroke-linecap="round"/></g>')
        if detail == "overview" or edge.medium == "subnet":
            continue
        # A wireless link's frequency, distance and signal matter most, so
        # they lead and survive when the label is shortened.
        text_parts = _link_label_parts(edge)
        # In the topology view a label must fit between the two devices.
        room = abs(a.anchor[0] - b.anchor[0]) - ICON - 24
        fit = max(12, int(room / 5.6)) if topology and room > 0 else 60
        topo_edge_label: str | None = None
        if topology and edge.medium != "wireless" and text_parts:
            # Rendered in the port-label loop below, beside the a-side port name.
            topo_edge_label = escape(_clip(" · ".join(text_parts), min(44, fit)))
        if edge.medium == "wireless":
            labels.append(f'<use href="#nm-radio-badge" xlink:href="#nm-radio-badge" x="{mid[0] - 9:.1f}" '
                          f'y="{mid[1] - 9:.1f}" width="18" height="18"/>')
            if text_parts:
                labels.append(_label("nm-edge-label-radio", mid[0], mid[1] + 22,
                                     escape(_clip(" · ".join(text_parts), min(60, fit)))))
        elif text_parts and topo_edge_label is None:
            # The non-topology view keeps the label at the line's midpoint.
            labels.append(_label("nm-edge-label", mid[0], mid[1] + 3,
                                 escape(_clip(" · ".join(text_parts), min(44, fit)))))
        for port, point, node in ((edge.a_port, spot_a, a), (edge.b_port, spot_b, b)):
            if not port and not (node is a and topo_edge_label):
                continue
            if topology:
                # Place the port label right next to the device icon, on the
                # side where the link leaves; stack multiple ports vertically.
                # When several links leave this device, push the uplink-side
                # label further out along its own curve so the labels fan
                # away instead of stacking on one point.
                peer = b if node is a else a
                if abs(node.anchor[0] - peer.anchor[0]) < 1 or node.anchor[0] < peer.anchor[0]:
                    x = node.anchor[0] + ICON / 2 + 8
                    anchor = "start"
                    fan_value = fan_t.get((edge.a, edge.b))
                    if fan_value is not None:
                        x += _topology_fan_x(a, b, bend, fan_value)
                else:
                    x = node.anchor[0] - ICON / 2 - 8
                    anchor = "end"
                slot, total = topo_slots[(node.id, index)]
                # Centre this side's block of port names and link labels on the
                # icon so several stacked labels stay clear of the device name.
                base = node.anchor[1] - (total - 1) * TOPO_LINE_H / 2
                y = base + slot * TOPO_LINE_H
                if port:
                    labels.append(_label("nm-port", x, y, escape(_clip(port, 18)), anchor=anchor))
                # The link's label sits on the line under its a-side port name,
                # so it reads as that port's annotation rather than floating on
                # the line between the two devices.
                if node is a and topo_edge_label:
                    labels.append(_label("nm-edge-label", x, y + (TOPO_LINE_H if port else 0),
                                         topo_edge_label, anchor=anchor))
                continue
            if edge.lane is not None:
                # Rack-internal links leave from the card's right edge; stack
                # their port names there so several links stay legible.
                slot = side_slots.get(node.id, 0)
                side_slots[node.id] = slot + 1
                labels.append(_label("nm-port", node.x + CARD_W + 3, node.anchor[1] - 10 + slot * 11,
                                     escape(_clip(port, 14)), anchor="start"))
                continue
            labels.append(_label("nm-port", point[0], point[1] + 3, escape(_clip(port, 18))))

    for node in graph.nodes.values():
        lines = _text_lines(node, detail)
        cx = node.x + CARD_W / 2
        node_attrs = f'class="nm-node" data-node-id="{_attr(node.id)}" data-type="{_attr(node.type_key)}"'
        out.append(f"<g {node_attrs}>")
        if node.url and not interactive:
            out.append(f'<a href="{_attr(node.url)}">')
        out.append(f"<title>{escape(node.label)} — {escape(node.type_label if node.kind != 'network' else 'Subnet')}</title>")
        # In the topology view devices are just an icon and labels; the card
        # stays as an invisible click target that is outlined when selected.
        card_style = ('fill-opacity="0" stroke="none"' if topology
                      else f'stroke="{node.type.colour}" stroke-opacity="0.55"')
        out.append(f'<rect class="nm-card" x="{node.x:.1f}" y="{node.y:.1f}" width="{CARD_W}" height="{node.h:.1f}" '
                   f'rx="8" {card_style}/>')
        out.append(f'<use href="#nm-icon-{node.type_key}" xlink:href="#nm-icon-{node.type_key}" '
                   f'x="{cx - ICON / 2:.1f}" y="{node.y + 8:.1f}" width="{ICON}" height="{ICON}"/>')
        if node.radio_count:
            out.append(f'<use href="#nm-radio-badge" xlink:href="#nm-radio-badge" x="{cx + ICON / 2 - 8:.1f}" '
                       f'y="{node.y + 2:.1f}" width="18" height="18"><title>{node.radio_count} radio interface'
                       f'{"s" if node.radio_count != 1 else ""}</title></use>')
        text_y = node.y + 8 + ICON + 6 + 10
        for css, text in lines:
            limit = 24 if css == "nm-name" else 30
            out.append(f'<text class="{css}" x="{cx:.1f}" y="{text_y:.1f}" text-anchor="middle">{escape(_clip(text, limit))}</text>')
            text_y += LINE_H
        if node.url and not interactive:
            out.append("</a>")
        out.append("</g>")
    out.extend(labels)

    if legend_height:
        y = height + 8
        out.append(f'<g class="nm-legend-block"><text class="nm-legend-title" x="{MARGIN}" y="{y + 10:.0f}">Legend</text>')
        y += 22
        for index, key in enumerate(legend_types):
            column, row = index % 5, index // 5
            lx, ly = MARGIN + column * 200, y + row * 24
            out.append(f'<use href="#nm-icon-{key}" xlink:href="#nm-icon-{key}" x="{lx}" y="{ly:.0f}" width="18" height="18"/>'
                       f'<text class="nm-legend" x="{lx + 24}" y="{ly + 13:.0f}">{escape(asset_types.get(key).label)}</text>')
        if legend_media:
            ly = y + math.ceil(len(legend_types) / 5) * 24
            for index, key in enumerate(legend_media):
                style = MEDIUM_STYLES[key]
                lx = MARGIN + index * 170
                dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
                out.append(f'<path d="M{lx},{ly + 9:.0f} h34" stroke="{style["colour"]}" stroke-width="{style["width"]}"{dash}/>'
                           f'<text class="nm-legend" x="{lx + 42}" y="{ly + 13:.0f}">{escape(style["label"])}</text>')
        out.append("</g>")
    out.append("</svg>")
    return _apply_presentation("".join(out))


def graph_payload(graph: Graph) -> dict[str, Any]:
    """Node and link details for the browser's side panel."""
    nodes = {}
    for node in graph.nodes.values():
        nodes[node.id] = {
            "id": node.id, "kind": node.kind, "label": node.label, "type": node.type_label,
            "icon": node.type.icon, "site": node.site, "rack": node.rack_name, "url": node.url,
            "ips": node.ips, "interfaces": node.interfaces,
            "facts": [{"label": label, "value": value} for label, value in node.facts],
            "links": [],
        }
    for edge in graph.edges:
        style = MEDIUM_STYLES.get(edge.medium, MEDIUM_STYLES["other"])
        for near, near_port, far, far_port in ((edge.a, edge.a_port, edge.b, edge.b_port),
                                               (edge.b, edge.b_port, edge.a, edge.a_port)):
            nodes[near]["links"].append({
                "port": near_port, "peer": graph.nodes[far].label, "peer_id": far, "peer_port": far_port,
                "medium": style["label"], "label": edge.label, "details": edge.details, "count": edge.count})
    return {"nodes": nodes}


def describe_options(options: MapOptions) -> str:
    parts = [f"{LAYOUTS[options.layout]['label']} layout", f"Detail: {DETAIL_LEVELS[options.detail]['label']}"]
    if options.types is not None:
        parts.append(f"{len(options.types)} device type{'s' if len(options.types) != 1 else ''}")
    else:
        parts.append("all device types")
    if options.site:
        parts.append(f"site: {options.site}")
    if options.show_subnets:
        parts.append("subnets shown")
    if options.include_unlinked:
        parts.append("including undocumented devices")
    if not options.hide_unlinked:
        parts.append("including unlinked devices")
    return " · ".join(parts)


def subtitle(company_name: str | None, options: MapOptions, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%d %b %Y %H:%M UTC")
    return " · ".join(part for part in (company_name, f"Generated {stamp}", describe_options(options)) if part)


def inventory(graph: Graph) -> list[dict[str, Any]]:
    """Rows for the PDF device inventory, in the order devices appear on the map."""
    payload = graph_payload(graph)["nodes"]
    rows = []
    for node in sorted(graph.nodes.values(), key=lambda item: (item.site, item.rack_name or "~", -item.start_unit, item.label.casefold())):
        if node.kind not in {"asset", "item"}:
            continue
        rows.append({
            "name": node.label, "type": node.type_label, "site": node.site, "rack": node.rack_name,
            "ips": node.ips, "interfaces": node.interfaces, "links": payload[node.id]["links"],
            "facts": node.facts,
        })
    return rows
