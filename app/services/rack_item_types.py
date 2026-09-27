"""Catalogue of equipment types that can be placed in a rack."""

from __future__ import annotations

from dataclasses import dataclass

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


ITEM_TYPES: tuple[RackItemType, ...] = (
    RackItemType("server", "Server", "Server",
                 connectors=(("data", 2), ("psu", 2)), rear_connectors=("data", "psu")),
    RackItemType("switch", "Network switch or router", "Switch / router",
                 connectors=(("data", 24), ("psu", 1)), rear_connectors=("psu",)),
    RackItemType("storage", "Storage array (SAN/NAS)", "Storage", default_height=2, image_units=2,
                 connectors=(("data", 4), ("psu", 2)), rear_connectors=("data", "psu")),
    RackItemType("patch_panel", "Patch panel", "Patch panel",
                 connectors=(("data", 24),), active=False),
    RackItemType("kvm", "KVM console", "KVM console",
                 connectors=(("kvm", 8), ("data", 1), ("psu", 1)),
                 rear_connectors=("kvm", "data", "psu")),
    RackItemType("pdu", "Power distribution unit (PDU)", "PDU",
                 connectors=(("iec", 8), ("3pin", 0), ("psu", 1)), rear_connectors=("psu",)),
    RackItemType("ups", "Uninterruptible power supply (UPS)", "UPS", default_height=2, image_units=2,
                 connectors=(("iec", 6), ("3pin", 2), ("psu", 1)),
                 rear_connectors=("iec", "3pin", "psu")),
    RackItemType("fan_tray", "Fan tray or ventilation", "Fan tray",
                 connectors=(("psu", 1),), rear_connectors=("psu",)),
    RackItemType("shelf", "Shelf or blanking panel", "Shelf / blank", active=False),
    RackItemType("cable_management", "Cable management ring or bar", "Cable management", active=False),
)

BY_KEY = {item_type.key: item_type for item_type in ITEM_TYPES}
# Items documented before the catalogue existed were stored as "device".
LEGACY_ALIASES = {"device": "server"}
DEFAULT_KEY = "server"
PORT_TYPE_KEYS = frozenset(item_type.key for item_type in ITEM_TYPES if item_type.has_ports)


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
