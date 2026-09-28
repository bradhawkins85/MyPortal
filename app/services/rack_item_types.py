"""Catalogue of equipment types that can be placed in a rack."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from app.services import asset_types

# Connector kinds a rack item port can be. Data ports are RJ45/SFP style;
# power outlets are either IEC (C13/C19) or 3-pin mains sockets; PSUs are
# power inputs fed from an outlet; KVM device ports connect managed devices.
CONNECTORS = {
    "data": {"label": "Port", "plural": "network ports", "count_label": "Network ports"},
    "iec": {"label": "IEC", "plural": "IEC outlets", "count_label": "IEC outlets"},
    "3pin": {"label": "3-pin", "plural": "3-pin outlets", "count_label": "3-pin outlets"},
    "psu": {"label": "PSU", "plural": "power supplies", "count_label": "Power supplies (PSUs)"},
    "kvm": {"label": "Device", "plural": "connected devices", "count_label": "Connected devices"},
}
OUTLET_CONNECTORS = frozenset({"iec", "3pin"})
POWER_INPUT = "psu"
WIDTHS = (1, 2, 3)


@dataclass(frozen=True)
class RackItemType:
    key: str
    label: str
    short_label: str
    default_height: int = 1
    # Connector kinds this type documents, with the default count of each.
    connectors: tuple[tuple[str, int], ...] = ()
    # Powered equipment shows a status LED; passive hardware does not.
    active: bool = True
    # Rack units covered by one repeat of the faceplate image.
    image_units: int = 1
    # Connectors physically on the back panel (e.g. server NICs and PSUs);
    # the rest are on the front.
    rear_connectors: tuple[str, ...] = ()
    # Picker group: an asset category, or rack hardware not tracked as an asset.
    category: str = "other"

    @property
    def asset_type(self) -> str:
        """The asset catalogue type this rack item type is drawn as elsewhere."""
        return self.key if self.key in asset_types.BY_KEY else asset_types.DEFAULT_KEY

    @property
    def has_ports(self) -> bool:
        return bool(self.connectors)

    @property
    def connector_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _count in self.connectors)

    @property
    def default_counts(self) -> dict[str, int]:
        return dict(self.connectors)

    @property
    def power_input(self) -> bool:
        """Whether the type has power supplies fed from another unit's outlets."""
        return POWER_INPUT in self.connector_keys

    @property
    def image(self) -> str:
        return image_path(self.key, 3, rear=False)

    @property
    def rear_image(self) -> str:
        return image_path(self.key, 3, rear=True)


def image_path(key: str, width_lanes: int, rear: bool = False) -> str:
    """Return the faceplate image for a type at a lane width and viewing side."""
    width = width_lanes if width_lanes in WIDTHS else 3
    return f"/static/images/racks/{key}-w{width}{'-rear' if rear else ''}.svg"


# Rack-specific details for the types that need more than the generic
# appliance defaults: connectors, height and which side the ports are on.
# Every asset type is offered, so a rack item uses the same types as assets.
_SPECS: dict[str, dict] = {
    "modem": dict(connectors=(("data", 2), ("psu", 1)), rear_connectors=("psu",)),
    "router": dict(connectors=(("data", 8), ("psu", 1)), rear_connectors=("psu",)),
    "firewall": dict(connectors=(("data", 8), ("psu", 2)), rear_connectors=("psu",)),
    "vpn_gateway": dict(connectors=(("data", 4), ("psu", 1)), rear_connectors=("psu",)),
    "switch": dict(connectors=(("data", 24), ("psu", 1)), rear_connectors=("psu",)),
    "patch_panel": dict(connectors=(("data", 24),), active=False),
    "load_balancer": dict(connectors=(("data", 8), ("psu", 2)), rear_connectors=("psu",)),
    "access_point": dict(connectors=(("data", 1),), rear_connectors=("data",)),
    "wireless_bridge": dict(connectors=(("data", 1),), rear_connectors=("data",)),
    "wireless_controller": dict(connectors=(("data", 4), ("psu", 1)), rear_connectors=("psu",)),
    "server": dict(connectors=(("data", 2), ("psu", 2)), rear_connectors=("data", "psu")),
    "hypervisor": dict(default_height=2, image_units=2, connectors=(("data", 4), ("psu", 2)),
                       rear_connectors=("data", "psu")),
    "virtual_machine": dict(connectors=(("data", 1),), rear_connectors=("data",)),
    "storage": dict(default_height=2, image_units=2, connectors=(("data", 4), ("psu", 2)),
                    rear_connectors=("data", "psu")),
    "workstation": dict(default_height=2, image_units=2, connectors=(("data", 1), ("psu", 1)),
                        rear_connectors=("data", "psu")),
    "laptop": dict(connectors=(("data", 1), ("psu", 1)), rear_connectors=("data", "psu")),
    "thin_client": dict(connectors=(("data", 1), ("psu", 1)), rear_connectors=("data", "psu")),
    "tablet": dict(connectors=(("psu", 1),), rear_connectors=("psu",)),
    "mobile_phone": dict(connectors=(("psu", 1),), rear_connectors=("psu",)),
    "printer": dict(default_height=3, image_units=3, connectors=(("data", 1), ("psu", 1)),
                    rear_connectors=("data", "psu")),
    "scanner": dict(default_height=2, image_units=2, connectors=(("data", 1), ("psu", 1)),
                    rear_connectors=("data", "psu")),
    "ip_phone": dict(default_height=2, image_units=2, connectors=(("data", 2),), rear_connectors=("data",)),
    "phone_system": dict(connectors=(("data", 8), ("psu", 1)), rear_connectors=("psu",)),
    "display": dict(default_height=2, image_units=2, connectors=(("psu", 1),), rear_connectors=("psu",)),
    "conference": dict(connectors=(("data", 2), ("psu", 1)), rear_connectors=("data", "psu")),
    "camera": dict(default_height=2, image_units=2, connectors=(("data", 1),), rear_connectors=("data",)),
    "nvr": dict(default_height=2, image_units=2, connectors=(("data", 2), ("psu", 1)),
                rear_connectors=("data", "psu")),
    "access_control": dict(connectors=(("data", 1), ("psu", 1)), rear_connectors=("data", "psu")),
    "ups": dict(default_height=2, image_units=2, connectors=(("iec", 6), ("3pin", 2), ("psu", 1)),
                rear_connectors=("iec", "3pin", "psu")),
    "pdu": dict(connectors=(("iec", 8), ("3pin", 0), ("psu", 1)), rear_connectors=("psu",)),
    "iot": dict(connectors=(("data", 1),), rear_connectors=("data",)),
    "cloud_service": dict(active=False),
    "other": dict(connectors=(("data", 1), ("psu", 1)), rear_connectors=("data", "psu")),
}
# Shorter picker captions where the asset label is long.
_SHORT_LABELS = {
    "vpn_gateway": "VPN gateway", "switch": "Switch", "load_balancer": "Load balancer",
    "access_point": "Access point", "wireless_bridge": "Wireless bridge",
    "wireless_controller": "Wireless controller", "hypervisor": "Virtualisation host",
    "storage": "Storage", "workstation": "Workstation", "phone_system": "Phone system",
    "conference": "Conference system", "access_control": "Access control",
    "iot": "IoT device", "other": "Other device",
}

RACK_CATEGORY = "rack"
CATEGORY_LABELS = {**{key: meta["label"] for key, meta in asset_types.CATEGORIES.items()},
                   RACK_CATEGORY: "Rack hardware"}

ITEM_TYPES: tuple[RackItemType, ...] = (
    *(RackItemType(item.key, item.label, _SHORT_LABELS.get(item.key, item.label),
                   category=item.category, **_SPECS.get(item.key, {}))
      for item in asset_types.ASSET_TYPES),
    # Rack hardware that is not tracked as an asset.
    RackItemType("kvm", "KVM console", "KVM console", category=RACK_CATEGORY,
                 connectors=(("kvm", 8), ("data", 1), ("psu", 1)),
                 rear_connectors=("kvm", "data", "psu")),
    RackItemType("fan_tray", "Fan tray or ventilation", "Fan tray", category=RACK_CATEGORY,
                 connectors=(("psu", 1),), rear_connectors=("psu",)),
    RackItemType("shelf", "Shelf or blanking panel", "Shelf / blank", category=RACK_CATEGORY, active=False),
    RackItemType("cable_management", "Cable management ring or bar", "Cable management",
                 category=RACK_CATEGORY, active=False),
)

BY_KEY = {item_type.key: item_type for item_type in ITEM_TYPES}
# Items documented before the catalogue existed were stored as "device".
LEGACY_ALIASES = {"device": "server"}
DEFAULT_KEY = "server"
PORT_TYPE_KEYS = frozenset(item_type.key for item_type in ITEM_TYPES if item_type.has_ports)


def grouped() -> list[dict[str, Any]]:
    """Return the catalogue grouped by category, in the asset picker's order."""
    groups: list[dict[str, Any]] = []
    for category, label in CATEGORY_LABELS.items():
        types = [item for item in ITEM_TYPES if item.category == category]
        if types:
            groups.append({"key": category, "label": label, "types": types})
    return groups


def for_asset(asset: Any) -> str:
    """Return the rack item type matching an asset's catalogue type."""
    key = asset_types.effective(asset) if isinstance(asset, Mapping) else str(asset or "")
    return key if key in BY_KEY else DEFAULT_KEY


def normalise(value: str | None) -> str:
    """Return the catalogue key for a submitted or stored item type."""
    key = (value or DEFAULT_KEY).strip().lower()
    key = LEGACY_ALIASES.get(key, key)
    if key not in BY_KEY:
        raise ValueError("Unknown rack item type")
    return key


def get(value: str | None) -> RackItemType:
    """Return the catalogue entry for a stored item type, defaulting to a server."""
    try:
        return BY_KEY[normalise(value)]
    except ValueError:
        return BY_KEY[DEFAULT_KEY]


def connector_label(connector: str, ordinal: int) -> str:
    """Return a port's display name, e.g. "Port 3", "IEC 2" or "3-pin 1"."""
    return f"{CONNECTORS.get(connector, CONNECTORS['data'])['label']} {ordinal}"
