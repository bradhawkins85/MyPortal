"""Network interfaces on assets and the links between them.

Racked equipment already documents its ports (``rack_equipment_ports``) and
port-to-port patching. This module covers everything else a network map
needs: devices outside racks (a wall-mounted switch, a rooftop wireless
bridge, a desk phone) get named interfaces, and links join any two endpoints
- an asset interface or a rack data port - over copper, fibre or radio.

Endpoints are written ``interface:<id>`` or ``rack_port:<id>``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.database import db
from app.services import rack_item_types

INTERFACE_KINDS: dict[str, str] = {
    "ethernet": "Ethernet",
    "sfp": "SFP / fibre",
    "wan": "WAN / internet",
    "radio": "Long-range radio",
    "wifi": "Wi-Fi radio",
    "virtual": "Virtual / VLAN",
}
# Radio interfaces carry wireless links. A radio can hold many links (an
# access point serving several stations); a wired port holds one.
RADIO_KINDS = frozenset({"radio", "wifi"})
RADIO_MODES: dict[str, str] = {
    "ptp_master": "Point-to-point master",
    "ptp_station": "Point-to-point station",
    "ptmp_ap": "Point-to-multipoint AP",
    "ptmp_station": "Point-to-multipoint station",
    "ap": "Wi-Fi access point",
    "client": "Wi-Fi client",
}
MEDIA: dict[str, str] = {
    "copper": "Copper (RJ45)",
    "fibre": "Fibre",
    "dac": "Direct-attach cable",
    "wireless": "Wireless",
    "vpn": "VPN / tunnel",
    "other": "Other",
}
ENDPOINT_KINDS = frozenset({"interface", "rack_port"})


@dataclass(frozen=True)
class Endpoint:
    kind: str
    id: int

    @classmethod
    def parse(cls, value: Any) -> "Endpoint":
        kind, _, raw_id = str(value or "").partition(":")
        if kind not in ENDPOINT_KINDS:
            raise ValueError("Choose an interface or rack port for each end of the link")
        try:
            record_id = int(raw_id)
        except ValueError as exc:
            raise ValueError("Choose an interface or rack port for each end of the link") from exc
        return cls(kind, record_id)

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"


def _int_or_none(value: Any, low: int, high: int, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number") from exc
    if not low <= number <= high:
        raise ValueError(f"{label} must be between {low} and {high}")
    return number


def _text(value: Any, limit: int) -> str | None:
    clean = str(value or "").strip()
    if len(clean) > limit:
        raise ValueError(f"Keep text fields to {limit} characters")
    return clean or None


def clean_interface(values: dict[str, Any]) -> dict[str, Any]:
    """Validate an interface form, dropping radio fields from wired interfaces."""
    name = _text(values.get("name"), 64)
    if not name:
        raise ValueError("Give the interface a name, e.g. eth0 or Radio 1")
    kind = str(values.get("kind") or "ethernet").strip()
    if kind not in INTERFACE_KINDS:
        raise ValueError("Unknown interface kind")
    cleaned: dict[str, Any] = {
        "name": name, "kind": kind,
        "mac_address": _text(values.get("mac_address"), 32),
        "speed_mbps": _int_or_none(values.get("speed_mbps"), 1, 1_000_000, "Speed"),
        "vlan": _text(values.get("vlan"), 64),
        "notes": _text(values.get("notes"), 255),
        "radio_mode": None, "frequency_mhz": None, "channel_width_mhz": None,
        "ssid": None, "azimuth_deg": None, "tx_power_dbm": None, "antenna_gain_dbi": None,
    }
    if kind in RADIO_KINDS:
        mode = str(values.get("radio_mode") or "").strip() or None
        if mode is not None and mode not in RADIO_MODES:
            raise ValueError("Unknown radio mode")
        cleaned.update({
            "radio_mode": mode,
            "frequency_mhz": _int_or_none(values.get("frequency_mhz"), 1, 300_000, "Frequency"),
            "channel_width_mhz": _int_or_none(values.get("channel_width_mhz"), 1, 10_000, "Channel width"),
            "ssid": _text(values.get("ssid"), 64),
            "azimuth_deg": _int_or_none(values.get("azimuth_deg"), 0, 359, "Azimuth"),
            "tx_power_dbm": _int_or_none(values.get("tx_power_dbm"), -40, 60, "Transmit power"),
            "antenna_gain_dbi": _int_or_none(values.get("antenna_gain_dbi"), -10, 60, "Antenna gain"),
        })
    return cleaned


INTERFACE_COLUMNS = ("name", "kind", "mac_address", "speed_mbps", "vlan", "notes", "radio_mode",
                     "frequency_mhz", "channel_width_mhz", "ssid", "azimuth_deg", "tx_power_dbm",
                     "antenna_gain_dbi")


async def list_interfaces(company_id: int, asset_id: int | None = None) -> list[dict[str, Any]]:
    sql = """SELECT i.*, a.name asset_name, ip.address ip_address
             FROM asset_interfaces i JOIN assets a ON a.id=i.asset_id
             LEFT JOIN ip_addresses ip ON ip.id=i.ip_address_id
             WHERE i.company_id=%s"""
    params: tuple[Any, ...] = (company_id,)
    if asset_id is not None:
        sql += " AND i.asset_id=%s"
        params += (asset_id,)
    return list(await db.fetch_all(sql + " ORDER BY a.name, i.name", params) or [])


async def _check_ip(company_id: int, asset_id: int, ip_address_id: int | None) -> None:
    if ip_address_id is None:
        return
    row = await db.fetch_one(
        "SELECT asset_id FROM ip_addresses WHERE id=%s AND company_id=%s", (ip_address_id, company_id))
    if not row:
        raise ValueError("That IP address is not documented for this company")
    if row.get("asset_id") not in (None, asset_id):
        raise ValueError("That IP address is assigned to another asset")


async def create_interface(company_id: int, asset_id: int, values: dict[str, Any],
                           ip_address_id: int | None = None) -> int:
    cleaned = clean_interface(values)
    if not await db.fetch_one("SELECT id FROM assets WHERE id=%s AND company_id=%s", (asset_id, company_id)):
        raise ValueError("Asset does not belong to this company")
    if await db.fetch_one("SELECT id FROM asset_interfaces WHERE asset_id=%s AND name=%s",
                          (asset_id, cleaned["name"])):
        raise ValueError("This asset already has an interface with that name")
    await _check_ip(company_id, asset_id, ip_address_id)
    columns = ("company_id", "asset_id", "ip_address_id", *INTERFACE_COLUMNS)
    return await db.execute_returning_lastrowid(
        "INSERT INTO asset_interfaces (" + ",".join(columns) + ") VALUES ("  # nosec B608
        + ",".join(["%s"] * len(columns)) + ")",
        (company_id, asset_id, ip_address_id, *(cleaned[key] for key in INTERFACE_COLUMNS)))


async def update_interface(company_id: int, interface_id: int, values: dict[str, Any],
                           ip_address_id: int | None = None) -> None:
    existing = await db.fetch_one(
        "SELECT asset_id, kind FROM asset_interfaces WHERE id=%s AND company_id=%s",
        (interface_id, company_id))
    if not existing:
        raise ValueError("Interface not found")
    cleaned = clean_interface(values)
    clash = await db.fetch_one(
        "SELECT id FROM asset_interfaces WHERE asset_id=%s AND name=%s AND id<>%s",
        (existing["asset_id"], cleaned["name"], interface_id))
    if clash:
        raise ValueError("This asset already has an interface with that name")
    was_radio = existing.get("kind") in RADIO_KINDS
    if was_radio != (cleaned["kind"] in RADIO_KINDS) and await _links_for(Endpoint("interface", interface_id)):
        raise ValueError("Remove this interface's links before switching between wired and radio")
    await _check_ip(company_id, int(existing["asset_id"]), ip_address_id)
    await db.execute(
        "UPDATE asset_interfaces SET ip_address_id=%s," + ",".join(f"{key}=%s" for key in INTERFACE_COLUMNS)  # nosec B608
        + " WHERE id=%s AND company_id=%s",
        (ip_address_id, *(cleaned[key] for key in INTERFACE_COLUMNS), interface_id, company_id))


async def delete_interface(company_id: int, interface_id: int) -> None:
    await db.execute(
        "DELETE FROM network_links WHERE company_id=%s AND ((a_kind='interface' AND a_id=%s) OR (b_kind='interface' AND b_id=%s))",
        (company_id, interface_id, interface_id))
    await db.execute("DELETE FROM asset_interfaces WHERE id=%s AND company_id=%s", (interface_id, company_id))


async def list_links(company_id: int) -> list[dict[str, Any]]:
    return list(await db.fetch_all(
        "SELECT * FROM network_links WHERE company_id=%s ORDER BY id", (company_id,)) or [])


async def _links_for(endpoint: Endpoint) -> list[dict[str, Any]]:
    return list(await db.fetch_all(
        "SELECT id FROM network_links WHERE (a_kind=%s AND a_id=%s) OR (b_kind=%s AND b_id=%s)",
        (endpoint.kind, endpoint.id, endpoint.kind, endpoint.id)) or [])


async def _resolve(company_id: int, endpoint: Endpoint) -> dict[str, Any]:
    """Return what an endpoint is: its owner, and whether it is a radio."""
    if endpoint.kind == "interface":
        row = await db.fetch_one(
            "SELECT id, asset_id, kind FROM asset_interfaces WHERE id=%s AND company_id=%s",
            (endpoint.id, company_id))
        if not row:
            raise ValueError("Interface not found")
        return {"owner": f"asset:{row['asset_id']}", "radio": row["kind"] in RADIO_KINDS}
    row = await db.fetch_one(
        """SELECT p.id, p.connector, p.peer_port_id, p.asset_id linked_asset_id,
                  e.asset_id, e.id equipment_id
           FROM rack_equipment_ports p JOIN rack_equipment e ON e.id=p.equipment_id
           WHERE p.id=%s AND p.company_id=%s""", (endpoint.id, company_id))
    if not row or str(row.get("connector") or "data") != "data":
        raise ValueError("Choose a network port on a rack item")
    if row.get("peer_port_id"):
        raise ValueError("That rack port is already patched to another rack port")
    owner = f"asset:{row['asset_id']}" if row.get("asset_id") else f"item:{row['equipment_id']}"
    linked = f"asset:{row['linked_asset_id']}" if row.get("linked_asset_id") else None
    return {"owner": owner, "radio": False, "linked_asset": linked}


async def create_link(company_id: int, a: Endpoint, b: Endpoint, values: dict[str, Any]) -> int:
    if a == b:
        raise ValueError("A link needs two different ends")
    medium = str(values.get("medium") or "copper").strip()
    if medium not in MEDIA:
        raise ValueError("Unknown link medium")
    end_a, end_b = await _resolve(company_id, a), await _resolve(company_id, b)
    if end_a["owner"] == end_b["owner"]:
        raise ValueError("Both ends are on the same device")
    radios = end_a["radio"] + end_b["radio"]
    if medium == "wireless" and radios != 2:
        raise ValueError("A wireless link joins two radio interfaces")
    if medium != "wireless" and radios:
        raise ValueError("Radio interfaces can only carry wireless links")
    for endpoint, end in ((a, end_a), (b, end_b)):
        if not end["radio"] and await _links_for(endpoint):
            raise ValueError("A wired port can only have one link; remove the existing link first")
    # The rack editor can record a port as "connected to asset X". A link to
    # one of X's interfaces is the precise version of that, so it replaces it;
    # a port recorded against a different asset has to be changed there first.
    for endpoint, end, other in ((a, end_a, end_b), (b, end_b, end_a)):
        if end.get("linked_asset") and end["linked_asset"] != other["owner"]:
            raise ValueError("That rack port is recorded as connected to another asset; update it in Racks first")
    if radios == 2 and await db.fetch_one(
            """SELECT id FROM network_links WHERE company_id=%s AND
               ((a_kind=%s AND a_id=%s AND b_kind=%s AND b_id=%s) OR (a_kind=%s AND a_id=%s AND b_kind=%s AND b_id=%s))""",
            (company_id, a.kind, a.id, b.kind, b.id, b.kind, b.id, a.kind, a.id)):
        raise ValueError("These radios are already linked")
    for endpoint, end in ((a, end_a), (b, end_b)):
        if end.get("linked_asset"):
            await db.execute("UPDATE rack_equipment_ports SET asset_id=NULL WHERE id=%s AND company_id=%s",
                             (endpoint.id, company_id))
    return await db.execute_returning_lastrowid(
        """INSERT INTO network_links
           (company_id,a_kind,a_id,b_kind,b_id,medium,label,speed_mbps,distance_m,frequency_mhz,signal_dbm,notes)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (company_id, a.kind, a.id, b.kind, b.id, medium, _text(values.get("label"), 191),
         _int_or_none(values.get("speed_mbps"), 1, 1_000_000, "Speed"),
         _int_or_none(values.get("distance_m"), 0, 1_000_000, "Distance"),
         _int_or_none(values.get("frequency_mhz"), 1, 300_000, "Frequency") if medium == "wireless" else None,
         _int_or_none(values.get("signal_dbm"), -150, 0, "Signal") if medium == "wireless" else None,
         _text(values.get("notes"), 255)))


async def delete_link(company_id: int, link_id: int) -> None:
    await db.execute("DELETE FROM network_links WHERE id=%s AND company_id=%s", (link_id, company_id))


async def endpoint_options(company_id: int) -> list[dict[str, Any]]:
    """Every linkable endpoint, labelled "<device> · <port>", for link pickers."""
    options = [
        {"value": f"interface:{row['id']}", "device": row["asset_name"], "port": row["name"],
         "radio": row["kind"] in RADIO_KINDS,
         "label": f"{row['asset_name']} · {row['name']}"
                  + (f" ({INTERFACE_KINDS.get(row['kind'], row['kind'])})" if row["kind"] != "ethernet" else "")}
        for row in await list_interfaces(company_id)]
    rows = await db.fetch_all(
        """SELECT p.id, p.port_number, p.peer_port_id, e.id equipment_id,
                  COALESCE(e.name, a.name) device, r.name rack_name
           FROM rack_equipment_ports p JOIN rack_equipment e ON e.id=p.equipment_id
           JOIN racks r ON r.id=e.rack_id LEFT JOIN assets a ON a.id=e.asset_id
           WHERE p.company_id=%s AND COALESCE(p.connector,'data')='data'
           ORDER BY r.name, device, e.id, p.port_number""", (company_id,)) or []
    ordinals: dict[int, int] = {}
    for row in rows:
        # Ordinals count every data port so "Port 5" matches the rack view,
        # even though ports already patched rack-to-rack are not offered.
        ordinals[row["equipment_id"]] = ordinals.get(row["equipment_id"], 0) + 1
        if row.get("peer_port_id"):
            continue
        port = rack_item_types.connector_label("data", ordinals[row["equipment_id"]])
        options.append({"value": f"rack_port:{row['id']}", "device": row["device"], "port": port,
                        "radio": False, "label": f"{row['device']} · {port} ({row['rack_name']})"})
    return options


async def load(company_id: int) -> dict[str, Any]:
    """Everything the network map draws that is not already in the rack/IPAM overview."""
    assets = list(await db.fetch_all(
        """SELECT id, name, type, asset_type, asset_type_source, form_factor, os_name, machine_type,
                  status, serial_number, location, mac_address, archived_at
           FROM assets WHERE company_id=%s ORDER BY name, id""", (company_id,)) or [])
    relationships = list(await db.fetch_all(
        """SELECT source_asset_id, target_id FROM asset_relationships
           WHERE company_id=%s AND target_type='asset' AND relationship_type='connected_to'""",
        (company_id,)) or [])
    return {"assets": assets, "interfaces": await list_interfaces(company_id),
            "links": await list_links(company_id), "relationships": relationships}
