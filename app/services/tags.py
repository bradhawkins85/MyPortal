"""Tag names and the rules that tag assets automatically.

Tags are one shared list of labels on assets and companies, used to pick the
devices and customers an automation or script applies to. A technician picks
tags by hand, and MyPortal adds the rule-based tags below from what it knows
about each asset (its catalogue type and operating system). Rule tags are
found by ``auto_key``, so renaming "Server" to "Servers" keeps the rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from app.services import asset_types

MAX_NAME_LENGTH = 64
COLOUR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class AutoTag:
    key: str
    name: str
    colour: str
    description: str


AUTO_TAGS: tuple[AutoTag, ...] = (
    AutoTag("server", "Server", "#059669", "Servers, virtualisation hosts and storage, and virtual machines running a server OS."),
    AutoTag("workstation", "Workstation", "#4f46e5", "Desktop computers, thin clients and desktop virtual machines."),
    AutoTag("laptop", "Laptop", "#2563eb", "Laptops, notebooks and convertibles."),
    AutoTag("virtual_machine", "Virtual machine", "#7c3aed", "Assets reported as virtual machines."),
    AutoTag("os_windows", "Windows", "#0284c7", "Assets running Microsoft Windows."),
    AutoTag("os_macos", "macOS", "#475569", "Assets running Apple macOS."),
    AutoTag("os_linux", "Linux", "#ca8a04", "Assets running a Linux distribution."),
)
AUTO_TAGS_BY_KEY: dict[str, AutoTag] = {item.key: item for item in AUTO_TAGS}

_SERVER_TYPES = {"server", "hypervisor", "storage"}
_WORKSTATION_TYPES = {"workstation", "thin_client"}
_WINDOWS = re.compile(r"\bwindows\b", re.I)
_MACOS = re.compile(r"\b(mac ?os|os ?x|darwin)\b", re.I)
_LINUX = re.compile(
    r"\b(linux|ubuntu|debian|centos|red ?hat|rhel|fedora|rocky|alma ?linux|suse|opensuse|mint|raspbian)\b",
    re.I,
)


def clean_name(value: Any) -> str:
    """Collapse whitespace in a tag name and check its length."""
    name = " ".join(str(value or "").split())
    if not name:
        raise ValueError("Tag name is required")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"Tag names can be at most {MAX_NAME_LENGTH} characters")
    return name


def slugify(value: Any) -> str:
    """The case- and spacing-folded form of a name, unique across tags."""
    return " ".join(str(value or "").split()).casefold()[:MAX_NAME_LENGTH]


def clean_colour(value: Any) -> str | None:
    colour = str(value or "").strip()
    if not colour:
        return None
    if not COLOUR_PATTERN.match(colour):
        raise ValueError("Colour must be a hex value such as #2563eb")
    return colour.lower()


def os_family(os_name: Any) -> str | None:
    text = str(os_name or "")
    if _WINDOWS.search(text):
        return "os_windows"
    if _MACOS.search(text):
        return "os_macos"
    if _LINUX.search(text):
        return "os_linux"
    return None


def auto_tag_keys(asset: Mapping[str, Any]) -> set[str]:
    """Return the rule-based tag keys an asset should carry."""
    keys: set[str] = set()
    catalogue = asset_types.effective(asset)
    os_name = str(asset.get("os_name") or "")
    virtual = catalogue == "virtual_machine" or str(asset.get("machine_type") or "").strip().lower() == "virtual"
    if virtual:
        keys.add("virtual_machine")
    if catalogue in _SERVER_TYPES:
        keys.add("server")
    elif catalogue in _WORKSTATION_TYPES:
        keys.add("workstation")
    elif catalogue == "laptop":
        keys.add("laptop")
    elif catalogue == "virtual_machine":
        # A VM is a server when its source or OS says so ("Virtual server", "Windows Server").
        if re.search(r"\bserver\b", f"{asset.get('type') or ''} {os_name}", re.I):
            keys.add("server")
        else:
            keys.add("workstation")
    family = os_family(os_name)
    if family:
        keys.add(family)
    return keys
