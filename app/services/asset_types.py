"""Fixed catalogue of asset types, their icons and how synced assets map to them.

Every asset has one catalogue type. Manual assets pick it from the list;
assets synchronised from Tactical RMM or Syncro get one derived from the
source's own type, chassis and operating system, which a technician can
override. The raw ``assets.type`` column is left as the source reported it
(billing counts "Workstation" and "Server" from it), and the catalogue key is
stored separately in ``assets.asset_type``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

ICON_DIR = Path(__file__).resolve().parents[1] / "static" / "images" / "asset-types"

# Categories group types in pickers and the network map legend. ``tier`` is the
# row a device of that category is drawn on in the network map: the internet
# edge at the top, then routing, switching, wireless, servers and endpoints.
CATEGORIES: dict[str, dict[str, Any]] = {
    "edge": {"label": "Internet edge", "colour": "#7c3aed", "tier": 0},
    "security": {"label": "Security", "colour": "#dc2626", "tier": 1},
    "network": {"label": "Network", "colour": "#0891b2", "tier": 2},
    "wireless": {"label": "Wireless", "colour": "#2563eb", "tier": 3},
    "server": {"label": "Servers and storage", "colour": "#059669", "tier": 4},
    "endpoint": {"label": "Computers and mobile", "colour": "#4f46e5", "tier": 5},
    "peripheral": {"label": "Printers, phones and AV", "colour": "#d97706", "tier": 5},
    "surveillance": {"label": "Cameras and access control", "colour": "#be185d", "tier": 5},
    "power": {"label": "Power", "colour": "#ca8a04", "tier": 4},
    "other": {"label": "Other", "colour": "#64748b", "tier": 5},
}


@dataclass(frozen=True)
class AssetType:
    key: str
    label: str
    category: str
    # Radio-capable types are offered a radio interface by default and are
    # drawn with an antenna so long-range links read clearly on the map.
    radio: bool = False
    # Words in a synced type, chassis or OS name that identify this type.
    aliases: tuple[str, ...] = ()
    # The icon file to draw, when it differs from the key.
    icon_key: str | None = None

    @property
    def icon(self) -> str:
        return f"/static/images/asset-types/{self.icon_key or self.key}.svg"

    @property
    def colour(self) -> str:
        return CATEGORIES[self.category]["colour"]

    @property
    def tier(self) -> int:
        return int(CATEGORIES[self.category]["tier"])

    @property
    def category_label(self) -> str:
        return str(CATEGORIES[self.category]["label"])


ASSET_TYPES: tuple[AssetType, ...] = (
    AssetType("modem", "Modem / NTD", "edge", aliases=("modem", "ntd", "nbn", "ont", "cable modem", "dsl")),
    AssetType("router", "Router", "edge", aliases=("router", "gateway", "edge router")),
    AssetType("firewall", "Firewall", "security", aliases=("firewall", "utm", "fortigate", "sonicwall", "pfsense", "opnsense", "sophos xg", "watchguard", "palo alto")),
    AssetType("vpn_gateway", "VPN gateway", "security", aliases=("vpn", "vpn gateway", "vpn concentrator")),
    AssetType("switch", "Network switch", "network", aliases=("switch", "network switch", "managed switch", "poe switch")),
    AssetType("patch_panel", "Patch panel", "network", aliases=("patch panel", "patch field")),
    AssetType("load_balancer", "Load balancer", "network", aliases=("load balancer", "adc")),
    AssetType("access_point", "Wireless access point", "wireless", radio=True, aliases=("access point", "wap", "wifi ap", "unifi ap", "wireless ap")),
    AssetType("wireless_bridge", "Point-to-point wireless bridge", "wireless", radio=True, aliases=("wireless bridge", "p2p", "ptp", "point to point", "point-to-point", "airfiber", "powerbeam", "nanobeam", "litebeam", "radio link", "microwave link")),
    AssetType("wireless_controller", "Wireless controller", "wireless", aliases=("wireless controller", "wlc", "wifi controller")),
    AssetType("server", "Server", "server", aliases=("server", "windows server", "rack server", "tower server")),
    AssetType("hypervisor", "Virtualisation host", "server", aliases=("hypervisor", "esxi", "proxmox", "hyper-v host", "virtualisation host", "virtualization host", "xenserver")),
    AssetType("virtual_machine", "Virtual machine", "server", aliases=("virtual machine", "vm", "virtual server")),
    AssetType("storage", "Storage (NAS / SAN)", "server", aliases=("nas", "san", "storage", "synology", "qnap", "file server")),
    AssetType("workstation", "Desktop workstation", "endpoint", aliases=("workstation", "desktop", "tower", "mini tower", "all in one", "all-in-one", "pc")),
    AssetType("laptop", "Laptop", "endpoint", aliases=("laptop", "notebook", "portable", "convertible", "detachable", "macbook")),
    AssetType("thin_client", "Thin client", "endpoint", aliases=("thin client", "zero client")),
    AssetType("tablet", "Tablet", "endpoint", aliases=("tablet", "ipad", "ipados")),
    AssetType("mobile_phone", "Mobile phone", "endpoint", aliases=("mobile", "smartphone", "iphone", "android", "ios", "mobile phone")),
    AssetType("printer", "Printer / MFP", "peripheral", aliases=("printer", "mfp", "multifunction", "copier", "managed printer", "label printer")),
    AssetType("scanner", "Scanner", "peripheral", aliases=("scanner",)),
    AssetType("ip_phone", "IP phone", "peripheral", aliases=("ip phone", "voip phone", "desk phone", "handset", "sip phone", "yealink", "polycom")),
    AssetType("phone_system", "Phone system / PBX", "peripheral", aliases=("pbx", "phone system", "voip gateway", "ata")),
    AssetType("display", "Display / TV", "peripheral", aliases=("display", "tv", "monitor", "screen", "projector", "digital signage")),
    AssetType("conference", "Conference room system", "peripheral", aliases=("conference", "video conferencing", "meeting room", "teams room", "zoom room")),
    AssetType("camera", "IP camera", "surveillance", aliases=("camera", "cctv", "ip camera", "ipcam")),
    AssetType("nvr", "NVR / DVR", "surveillance", aliases=("nvr", "dvr", "video recorder")),
    AssetType("access_control", "Door / access control", "surveillance", aliases=("access control", "door controller", "intercom", "alarm panel", "alarm")),
    AssetType("ups", "UPS", "power", aliases=("ups", "uninterruptible power")),
    AssetType("pdu", "PDU", "power", aliases=("pdu", "power distribution")),
    AssetType("iot", "IoT / smart device", "other", aliases=("iot", "smart device", "sensor", "thermostat", "controller", "plc", "building management")),
    AssetType("cloud_service", "Cloud service", "other", aliases=("cloud", "saas", "cloud service", "hosted")),
    AssetType("other", "Other device", "other"),
)

# Types typed in by hand (ASSET_TYPE_MODE custom or manual) are stored under
# this key with their name in ``assets.type``. It is not offered in pickers or
# matched from synced data, and it is drawn with the generic icon.
CUSTOM_KEY = "custom"
CUSTOM_TYPE = AssetType(CUSTOM_KEY, "Custom type", "other", icon_key="other")

BY_KEY: dict[str, AssetType] = {item.key: item for item in (*ASSET_TYPES, CUSTOM_TYPE)}
DEFAULT_KEY = "other"
MODES = ("auto", "custom", "manual")
SOURCES = ("auto", "manual")

def get(key: str | None) -> AssetType:
    """Return the catalogue entry for ``key``, defaulting to "Other device"."""
    return BY_KEY.get(str(key or "").strip().lower(), BY_KEY[DEFAULT_KEY])


def display_label(asset: Mapping[str, Any]) -> str:
    """Return the name to show for an asset's type, using a custom type's own name."""
    key = effective(asset)
    if key == CUSTOM_KEY:
        return str(asset.get("type") or "").strip() or CUSTOM_TYPE.label
    return get(key).label


def resolve_name(name: str | None, *, mode: str, existing: list[str] | tuple[str, ...] = ()) -> tuple[str, str]:
    """Resolve a typed-in type name to ``(catalogue key, label)``.

    In custom mode a name matching a catalogue key or label uses that entry.
    Otherwise the name becomes a custom type, reusing the spelling of an
    ``existing`` custom type that differs only in case or spacing.
    """
    clean = " ".join(str(name or "").split())
    if not clean:
        raise ValueError("Asset type is required")
    if len(clean) > 255:
        raise ValueError("Asset type is too long")
    folded = clean.casefold()
    if mode != "manual":
        for item in ASSET_TYPES:
            if folded in {item.key.casefold(), item.label.casefold()}:
                return item.key, item.label
    for label in existing:
        if " ".join(str(label).split()).casefold() == folded:
            return CUSTOM_KEY, str(label)
    return CUSTOM_KEY, clean


def normalise(key: str | None) -> str:
    """Validate a submitted catalogue key (custom types are named, not keyed)."""
    clean = str(key or "").strip().lower()
    if clean not in BY_KEY or clean == CUSTOM_KEY:
        raise ValueError("Unknown asset type")
    return clean


def grouped() -> list[dict[str, Any]]:
    """Return the catalogue grouped by category, for ``<optgroup>`` pickers."""
    groups: list[dict[str, Any]] = []
    for category, meta in CATEGORIES.items():
        items = [item for item in ASSET_TYPES if item.category == category]
        if items:
            groups.append({"key": category, "label": meta["label"], "types": items})
    return groups


def _words(value: Any) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip() + " "


def match_text(value: Any) -> str | None:
    """Return the catalogue key a free-text type or model name names, if any.

    The longest matching alias wins, so "wireless access point" beats
    "access point" beats "point", and "virtual server" beats "server".
    """
    text = _words(value)
    if not text.strip():
        return None
    best: tuple[int, str] | None = None
    for item in ASSET_TYPES:
        for alias in (item.key.replace("_", " "), item.label, *item.aliases):
            words = _words(alias)
            if words.strip() and words in text and (best is None or len(words) > best[0]):
                best = (len(words), item.key)
    return best[1] if best else None


_DESKTOP_OS = re.compile(r"\b(windows|mac ?os|os x|ubuntu|debian|fedora|linux|chrome ?os)\b", re.I)


def derive(
    type_value: Any = None, *, form_factor: Any = None, os_name: Any = None,
    machine_type: Any = None,
) -> str:
    """Pick a catalogue type for an asset from what its source reported.

    Tactical RMM reports ``monitoring_type`` of "workstation" or "server";
    the chassis (form factor) then tells a laptop from a desktop and the
    machine type tells a virtual machine from hardware. Syncro and manual
    assets carry free-text types such as "Managed Printer" or "Switch".
    """
    raw = match_text(type_value)
    virtual = str(machine_type or "").strip().lower() == "virtual"
    chassis = match_text(form_factor)
    os_match = match_text(os_name)
    if raw in {None, "workstation", "server"}:
        if virtual and raw == "workstation":
            return "virtual_machine"
        if raw == "server" or (raw is None and os_match == "server"):
            return "server"
        if chassis in {"laptop", "tablet", "workstation", "thin_client"}:
            return chassis
        if raw == "workstation":
            return "workstation"
        if os_match in {"mobile_phone", "tablet"}:
            return os_match
        if virtual:
            return "virtual_machine"
        if raw is None and _DESKTOP_OS.search(str(os_name or "")):
            return "workstation"
        return raw or chassis or DEFAULT_KEY
    return raw


def effective(asset: Mapping[str, Any]) -> str:
    """Return the stored catalogue type for an asset, deriving one if unset."""
    stored = str(asset.get("asset_type") or "").strip().lower()
    if stored in BY_KEY:
        return stored
    return derive(asset.get("type"), form_factor=asset.get("form_factor"),
                  os_name=asset.get("os_name"), machine_type=asset.get("machine_type"))


@lru_cache(maxsize=None)
def icon_markup(key: str) -> str:
    """Return the inner markup of a type's icon (a 48x48 viewBox), for embedding."""
    item = get(key)
    path = ICON_DIR / f"{item.icon_key or item.key}.svg"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r"<svg[^>]*>(.*)</svg>", text, re.S)
    return match.group(1).strip() if match else ""
