"""Catalogue of equipment types that can be placed in a rack."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RackItemType:
    key: str
    label: str
    short_label: str
    default_height: int = 1
    # Label for numbered connection points ("ports", "outlets"); None when the
    # type has nothing to document.
    ports_label: str | None = None
    default_ports: int = 0
    # Powered equipment shows a status LED; passive hardware does not.
    active: bool = True
    # Rack units covered by one repeat of the faceplate image.
    image_units: int = 1

    @property
    def has_ports(self) -> bool:
        return self.ports_label is not None

    @property
    def image(self) -> str:
        return f"/static/images/racks/{self.key}.svg"


ITEM_TYPES: tuple[RackItemType, ...] = (
    RackItemType("server", "Server", "Server"),
    RackItemType("switch", "Network switch or router", "Switch / router",
                 ports_label="ports", default_ports=24),
    RackItemType("storage", "Storage array (SAN/NAS)", "Storage", default_height=2, image_units=2),
    RackItemType("patch_panel", "Patch panel", "Patch panel",
                 ports_label="ports", default_ports=24, active=False),
    RackItemType("kvm", "KVM console", "KVM console"),
    RackItemType("pdu", "Power distribution unit (PDU)", "PDU",
                 ports_label="outlets", default_ports=8),
    RackItemType("ups", "Uninterruptible power supply (UPS)", "UPS", default_height=2, image_units=2),
    RackItemType("fan_tray", "Fan tray or ventilation", "Fan tray"),
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
