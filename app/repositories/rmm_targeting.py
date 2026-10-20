"""Load the server-side inventory used to decide which RMM checks apply.

Contexts contain only targeting data: enrolment tokens, integration credentials,
and other company settings are never copied into them. All related records are
scoped to the supplied agents' companies and valid asset/company associations.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from app.core.database import db
from app.services import asset_types

_BATCH_SIZE = 400
_GIB = 1024 ** 3
_SOFTWARE_UNAVAILABLE_PREFIXES = ("software:", "powershell:", "system_profiler:")
_NETWORK_UNAVAILABLE_PREFIXES = ("network:", "powershell:", "system_profiler:")
_ASSET_FIELDS = (
    "id", "name", "type", "machine_type", "serial_number", "status", "location",
    "os_name", "cpu_name", "ram_gb", "form_factor", "last_user", "hdd_size",
    "approx_age", "performance_score", "warranty_status", "warranty_end_date",
    "criticality", "owner", "support_contact", "review_status", "last_sync",
    "boot_time",
)
_AGENT_FIELDS = (
    "id", "hostname", "os", "os_version", "status", "last_ip", "last_seen_utc",
)
_CAPACITY = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(GB|GiB|TB|TiB)?\s*", re.I)


def _identifier(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def _batches(values: Iterable[int]) -> Iterable[tuple[int, ...]]:
    ordered = sorted(set(values))
    for index in range(0, len(ordered), _BATCH_SIZE):
        yield tuple(ordered[index:index + _BATCH_SIZE])


def _marks(values: tuple[int, ...]) -> str:
    return ", ".join("%s" for _ in values)


def _capacity_gb(value: Any) -> float | None:
    """Accept unambiguous capacities; model names and disk descriptions are unknown."""
    if isinstance(value, bool) or value is None:
        return None
    match = _CAPACITY.fullmatch(str(value))
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "GB").casefold()
    if unit in {"tb", "tib"}:
        number *= 1024
    return number if math.isfinite(number) and number >= 0 else None


def _details(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, RecursionError):
            return None
    if not isinstance(value, Mapping):
        return None
    errors = value.get("errors", [])
    if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
        return None
    return dict(value)


def _collection_failed(details: Mapping[str, Any], prefixes: tuple[str, ...]) -> bool:
    return any(error.strip().casefold().startswith(prefixes) for error in details.get("errors", []))


def _gigabytes(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number / _GIB if math.isfinite(number) and number >= 0 else None


def _uptime_days(value: Any) -> float | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - value).total_seconds() / 86400)


def _ip_address(value: Any) -> str | None:
    try:
        # Agent network adapters report both plain addresses and CIDR notation.
        return str(ipaddress.ip_interface(str(value)).ip)
    except ValueError:
        return None


def _custom_value(row: Mapping[str, Any]) -> Any:
    field_type = row.get("field_type")
    if field_type == "checkbox":
        value = row.get("value_boolean")
        return bool(value) if value is not None else None
    if field_type == "date":
        value = row.get("value_date")
        return value.isoformat() if hasattr(value, "isoformat") else value
    return row.get("value_text")


def _boolean(value: Any) -> bool | None:
    """Missing or unrecognised flags stay unknown instead of becoming false."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalised = value.strip().casefold()
        if normalised in {"true", "1"}:
            return True
        if normalised in {"false", "0"}:
            return False
    return None


def _map_value(mapping: dict[str, Any], row: Mapping[str, Any], value: Any) -> None:
    name = row.get("name")
    # Digit-only keys are reserved for stable definition IDs, even when the
    # corresponding definition has no recorded value. Numeric field names
    # remain addressable by their own definition ID through the rule builder.
    if isinstance(name, str) and name and not name.isdigit():
        mapping[name] = value
    definition_id = _identifier(row.get("definition_id"))
    if definition_id is not None:
        mapping[str(definition_id)] = value


async def contexts_for_agents(agents: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Return inventory contexts keyed by agent ID, excluding archived companies.

    Related rows are loaded in bounded batches, so evaluating many checks does
    not issue a query for every individual condition or device. A missing or
    cross-company asset produces unknown asset inventory, never a fallback to
    another company's records. This result is for server-side evaluation only.
    """
    candidates: dict[int, dict[str, Any]] = {}
    for agent in agents:
        agent_id = _identifier(agent.get("id"))
        company_id = _identifier(agent.get("company_id"))
        if agent_id is not None and company_id is not None:
            candidates[agent_id] = {**agent, "id": agent_id, "company_id": company_id}
    if not candidates:
        return {}

    current_agents: dict[int, dict[str, Any]] = {}
    for batch in _batches(candidates):
        rows = await db.fetch_all(
            "SELECT id, company_id, asset_id, hostname, os, os_version, arch, agent_version, "
            "status, last_ip, last_seen_utc FROM rmm_agents "
            f"WHERE id IN ({_marks(batch)}) AND status = 'active'", batch,  # nosec B608
        )
        for row in rows or []:
            agent_id = int(row["id"])
            source = candidates[agent_id]
            if (_identifier(row.get("company_id")) == source["company_id"]
                    and _identifier(row.get("asset_id")) == _identifier(source.get("asset_id"))):
                current_agents[agent_id] = dict(row)
    candidates = current_agents
    if not candidates:
        return {}

    companies: dict[int, dict[str, Any]] = {}
    for batch in _batches(agent["company_id"] for agent in candidates.values()):
        rows = await db.fetch_all(
            f"SELECT c.* FROM companies c WHERE c.id IN ({_marks(batch)}) "  # nosec B608 - bound IDs
            "AND COALESCE(c.archived, 0) = 0", batch,
        )
        for row in rows or []:
            company_id = int(row["id"])
            companies[company_id] = {
                "id": company_id, "name": row.get("name"),
                "vip": _boolean(row.get("is_vip")), "agent_count": 0,
                "archived": False, "pending_approval": _boolean(row.get("pending_approval")),
                "is_demo": _boolean(row.get("is_demo")),
            }
    candidates = {key: agent for key, agent in candidates.items() if agent["company_id"] in companies}
    if not candidates:
        return {}

    inventory: dict[int, dict[str, Any]] = {}
    software: dict[int, list[str] | None] = {key: None for key in candidates}
    software_versions: dict[int, dict[str, list[str]] | None] = {key: None for key in candidates}
    software_publishers: dict[int, dict[str, list[str]] | None] = {key: None for key in candidates}
    for batch in _batches(candidates):
        rows = await db.fetch_all(
            f"SELECT * FROM rmm_device_inventory WHERE agent_id IN ({_marks(batch)})", batch,  # nosec B608
        )
        for row in rows or []:
            agent_id = int(row["agent_id"])
            agent = candidates[agent_id]
            if (not row.get("received_at")
                    or _identifier(row.get("company_id")) != agent["company_id"]
                    or _identifier(row.get("asset_id")) != _identifier(agent.get("asset_id"))):
                continue
            snapshot = dict(row)
            snapshot["details"] = _details(row.get("details_json"))
            inventory[agent_id] = snapshot
            if (snapshot["details"] is None
                    or _collection_failed(snapshot["details"], _SOFTWARE_UNAVAILABLE_PREFIXES)):
                continue
            software[agent_id], software_versions[agent_id], software_publishers[agent_id] = [], {}, {}
        known_ids = tuple(agent_id for agent_id in batch if software[agent_id] is not None)
        if known_ids:
            rows = await db.fetch_all(
                "SELECT agent_id, name, version, publisher FROM rmm_device_software "
                f"WHERE agent_id IN ({_marks(known_ids)}) ORDER BY name, version", known_ids,  # nosec B608
            )
            for row in rows or []:
                agent_id, name = int(row["agent_id"]), str(row["name"])
                if name not in software[agent_id]:
                    software[agent_id].append(name)
                for key, values in (("version", software_versions), ("publisher", software_publishers)):
                    value = row.get(key)
                    if value is not None and str(value):
                        collection = values[agent_id].setdefault(name, [])
                        if str(value) not in collection:
                            collection.append(str(value))

    company_variables: dict[int, dict[str, Any]] = {key: {} for key in companies}
    company_tags: dict[int, set[str]] = {key: set() for key in companies}
    company_tag_ids: dict[int, dict[int, str]] = {key: {} for key in companies}
    for batch in _batches(companies):
        rows = await db.fetch_all(
            "SELECT company_id, COUNT(*) AS agent_count FROM rmm_agents "
            f"WHERE company_id IN ({_marks(batch)}) AND status = 'active' GROUP BY company_id",  # nosec B608
            batch,
        )
        for row in rows or []:
            companies[int(row["company_id"])]["agent_count"] = int(row["agent_count"])
        rows = await db.fetch_all(
            "SELECT d.*, d.id AS definition_id, v.company_id, v.value FROM company_variable_values v "
            "JOIN company_variable_definitions d ON d.id = v.variable_id "
            f"WHERE v.company_id IN ({_marks(batch)})", batch,  # nosec B608
        )
        for row in rows or []:
            # Definitions currently have no secret metadata; respect it if added.
            if row.get("is_secret") or row.get("sensitive") or row.get("field_type") == "secret":
                continue
            _map_value(company_variables[int(row["company_id"])], row, row.get("value"))
        rows = await db.fetch_all(
            "SELECT ct.company_id, t.id AS tag_id, t.name FROM company_tags ct "
            "JOIN tags t ON t.id = ct.tag_id "
            f"WHERE ct.company_id IN ({_marks(batch)})", batch,  # nosec B608
        )
        for row in rows or []:
            company_id = int(row["company_id"])
            name = str(row["name"])
            company_tags[company_id].add(name)
            company_tag_ids[company_id][int(row["tag_id"])] = name

    requested_assets: dict[int, set[int]] = {}
    for agent in candidates.values():
        asset_id = _identifier(agent.get("asset_id"))
        if asset_id is not None:
            requested_assets.setdefault(asset_id, set()).add(agent["company_id"])
    assets: dict[int, dict[str, Any]] = {}
    asset_companies: dict[int, int] = {}
    linked_agents = (agent_id for agent_id, agent in candidates.items() if _identifier(agent.get("asset_id")))
    for batch in _batches(linked_agents):
        rows = await db.fetch_all(
            "SELECT a.* FROM assets a JOIN rmm_agents g "
            "ON g.asset_id = a.id AND g.company_id = a.company_id "
            f"WHERE g.id IN ({_marks(batch)}) AND a.archived_at IS NULL",  # nosec B608
            batch,
        )
        for row in rows or []:
            asset_id, company_id = int(row["id"]), int(row["company_id"])
            if company_id not in requested_assets.get(asset_id, set()):
                continue
            asset = {key: row.get(key) for key in _ASSET_FIELDS}
            asset["catalogue_type"] = asset_types.effective(row)
            asset["manufacturer"] = row.get("motherboard_manufacturer")
            asset["disk_gb"] = _capacity_gb(row.get("hdd_size"))
            asset["ip_address"] = []
            assets[asset_id], asset_companies[asset_id] = asset, company_id

    asset_custom: dict[int, dict[str, Any]] = {key: {} for key in assets}
    asset_direct_tags: dict[int, set[str]] = {key: set() for key in assets}
    asset_blocks: dict[int, set[int]] = {key: set() for key in assets}
    for batch in _batches(assets):
        rows = await db.fetch_all(
            "SELECT v.asset_id, d.id AS definition_id, d.name, d.field_type, "
            "v.value_text, v.value_date, v.value_boolean FROM asset_custom_field_values v "
            "JOIN asset_custom_field_definitions d ON d.id = v.field_definition_id "
            f"WHERE v.asset_id IN ({_marks(batch)})", batch,  # nosec B608
        )
        for row in rows or []:
            _map_value(asset_custom[int(row["asset_id"])], row, _custom_value(row))
        rows = await db.fetch_all(
            "SELECT at.asset_id, t.name FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
            f"WHERE at.asset_id IN ({_marks(batch)})", batch,  # nosec B608
        )
        for row in rows or []:
            asset_direct_tags[int(row["asset_id"])].add(str(row["name"]))
        rows = await db.fetch_all(
            f"SELECT asset_id, tag_id FROM asset_tag_blocks WHERE asset_id IN ({_marks(batch)})",  # nosec B608
            batch,
        )
        for row in rows or []:
            asset_blocks[int(row["asset_id"])].add(int(row["tag_id"]))
        rows = await db.fetch_all(
            "SELECT ip.asset_id, ip.company_id, ip.address FROM ip_addresses ip "
            f"WHERE ip.asset_id IN ({_marks(batch)}) "  # nosec B608
            "UNION SELECT i.asset_id, i.company_id, ip.address FROM asset_interfaces i "
            "JOIN ip_addresses ip ON ip.id = i.ip_address_id AND ip.company_id = i.company_id "
            f"WHERE i.asset_id IN ({_marks(batch)})", batch + batch,  # nosec B608
        )
        for row in rows or []:
            asset_id = int(row["asset_id"])
            if row.get("company_id") != asset_companies[asset_id]:
                continue
            address = _ip_address(row.get("address"))
            if address is None:
                continue
            addresses = assets[asset_id]["ip_address"]
            if address not in addresses:
                addresses.append(address)

    contexts: dict[int, dict[str, Any]] = {}
    for agent_id, source in candidates.items():
        company_id = source["company_id"]
        asset_id = _identifier(source.get("asset_id"))
        asset = assets.get(asset_id) if asset_companies.get(asset_id) == company_id else None
        public_agent = {key: source.get(key) for key in _AGENT_FIELDS}
        public_agent["architecture"] = source.get("arch")
        public_agent["version"] = source.get("agent_version")
        canonical_asset = asset
        if asset is not None:
            asset = {**asset, "ip_address": list(asset["ip_address"]), "os_version": source.get("os_version")}
            names = asset_direct_tags[asset_id] | {
                name for tag_id, name in company_tag_ids[company_id].items()
                if tag_id not in asset_blocks[asset_id]
            }
        else:
            names = None
        snapshot = inventory.get(agent_id)
        known_empty_addresses = False
        incomplete_fields = []
        if snapshot is not None:
            asset = dict(asset) if asset is not None else {key: None for key in _ASSET_FIELDS}
            asset.setdefault("ip_address", [])
            for native_key, target_key in (
                ("manufacturer", "manufacturer"), ("model", "model"), ("serial_number", "serial_number"),
                ("cpu_model", "cpu_name"), ("cpu_cores", "cpu_cores"),
                ("cpu_logical_processors", "cpu_logical_processors"), ("bios_version", "bios_version"),
                ("domain", "domain"), ("os_name", "os_name"), ("os_version", "os_version"), ("os_build", "os_build"),
            ):
                if snapshot.get(native_key) is not None:
                    asset[target_key] = snapshot[native_key]
            for native_key, target_key in (("ram_bytes", "ram_gb"), ("disk_total_bytes", "disk_gb"),
                                            ("system_disk_free_bytes", "disk_free_gb")):
                value = _gigabytes(snapshot.get(native_key))
                if value is not None:
                    asset[target_key] = value
            asset["uptime_days"] = _uptime_days(snapshot.get("last_boot_at"))
            if canonical_asset is None:
                asset["catalogue_type"] = asset_types.derive(os_name=asset.get("os_name"))
            details = snapshot["details"]
            if details is None or _collection_failed(details, _NETWORK_UNAVAILABLE_PREFIXES):
                incomplete_fields.append("asset.ip_address")
            adapters = details.get("network_adapters") if details is not None else None
            known_empty_addresses = (
                isinstance(adapters, list) and not adapters
                and not _collection_failed(details, _NETWORK_UNAVAILABLE_PREFIXES)
            )
            for adapter in adapters if isinstance(adapters, list) else []:
                if not isinstance(adapter, Mapping):
                    continue
                for value in adapter.get("addresses") or []:
                    address = _ip_address(value)
                    if address is not None and address not in asset["ip_address"]:
                        asset["ip_address"].append(address)
            public_agent["hostname"] = snapshot.get("hostname") or public_agent.get("hostname")
            public_agent["architecture"] = snapshot.get("arch") or public_agent.get("architecture")
            public_agent["os_version"] = snapshot.get("os_version") or public_agent.get("os_version")
        if asset is not None and not asset["ip_address"] and not known_empty_addresses:
            asset["ip_address"] = None
        contexts[agent_id] = {
            "agent": public_agent, "asset": asset, "company": companies[company_id],
            "software": software[agent_id],
            "software_versions": software_versions[agent_id],
            "software_publishers": software_publishers[agent_id],
            "asset_tags": sorted(names) if names is not None else None,
            "company_tags": sorted(company_tags[company_id]),
            "asset_custom": asset_custom.get(asset_id) if canonical_asset is not None else None,
            "company_variables": company_variables[company_id],
            "incomplete_fields": incomplete_fields,
        }
    return contexts


async def context_for_asset(
    company_id: Any, asset_id: Any, *, agent: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a rule context for a device that has no active RMM agent.

    Tray devices are not always RMM agents, so this loads the company, its
    tags and variables, and (when linked) the asset with its tags and custom
    fields, in the shape :func:`contexts_for_agents` produces. Software
    inventory is unknown here, so software rules never match. ``agent``
    supplies whatever hostname/OS/IP the caller knows about the device.
    Archived companies return ``None``; an asset in another company or an
    archived asset leaves the asset inventory unknown.
    """
    company_id = _identifier(company_id)
    if company_id is None:
        return None
    row = await db.fetch_one(
        "SELECT c.* FROM companies c WHERE c.id = %s AND COALESCE(c.archived, 0) = 0", (company_id,),
    )
    if not row:
        return None
    company = {
        "id": company_id, "name": row.get("name"),
        "vip": _boolean(row.get("is_vip")), "agent_count": 0,
        "archived": False, "pending_approval": _boolean(row.get("pending_approval")),
        "is_demo": _boolean(row.get("is_demo")),
    }
    count = await db.fetch_one(
        "SELECT COUNT(*) AS agent_count FROM rmm_agents WHERE company_id = %s AND status = 'active'",
        (company_id,),
    )
    company["agent_count"] = int((count or {}).get("agent_count") or 0)
    company_variables: dict[str, Any] = {}
    for variable in await db.fetch_all(
        "SELECT d.*, d.id AS definition_id, v.value FROM company_variable_values v "
        "JOIN company_variable_definitions d ON d.id = v.variable_id WHERE v.company_id = %s",
        (company_id,),
    ) or []:
        if variable.get("is_secret") or variable.get("sensitive") or variable.get("field_type") == "secret":
            continue
        _map_value(company_variables, variable, variable.get("value"))
    company_tag_ids: dict[int, str] = {
        int(tag["tag_id"]): str(tag["name"])
        for tag in await db.fetch_all(
            "SELECT ct.company_id, t.id AS tag_id, t.name FROM company_tags ct "
            "JOIN tags t ON t.id = ct.tag_id WHERE ct.company_id = %s", (company_id,),
        ) or []
    }

    asset = None
    asset_custom = None
    asset_tags = None
    asset_id = _identifier(asset_id)
    if asset_id is not None:
        row = await db.fetch_one(
            "SELECT a.* FROM assets a WHERE a.id = %s AND a.company_id = %s AND a.archived_at IS NULL",
            (asset_id, company_id),
        )
        if row:
            asset = {key: row.get(key) for key in _ASSET_FIELDS}
            asset["catalogue_type"] = asset_types.effective(row)
            asset["manufacturer"] = row.get("motherboard_manufacturer")
            asset["disk_gb"] = _capacity_gb(row.get("hdd_size"))
            addresses: list[str] = []
            for ip in await db.fetch_all(
                "SELECT ip.address, ip.company_id FROM ip_addresses ip WHERE ip.asset_id = %s "
                "UNION SELECT ip.address, i.company_id FROM asset_interfaces i "
                "JOIN ip_addresses ip ON ip.id = i.ip_address_id AND ip.company_id = i.company_id "
                "WHERE i.asset_id = %s", (asset_id, asset_id),
            ) or []:
                address = _ip_address(ip.get("address"))
                if ip.get("company_id") == company_id and address is not None and address not in addresses:
                    addresses.append(address)
            asset["ip_address"] = addresses or None
            asset_custom = {}
            for value in await db.fetch_all(
                "SELECT v.asset_id, d.id AS definition_id, d.name, d.field_type, "
                "v.value_text, v.value_date, v.value_boolean FROM asset_custom_field_values v "
                "JOIN asset_custom_field_definitions d ON d.id = v.field_definition_id "
                "WHERE v.asset_id = %s", (asset_id,),
            ) or []:
                _map_value(asset_custom, value, _custom_value(value))
            direct = {
                str(tag["name"]) for tag in await db.fetch_all(
                    "SELECT t.name FROM asset_tags at JOIN tags t ON t.id = at.tag_id WHERE at.asset_id = %s",
                    (asset_id,),
                ) or []
            }
            blocked = {
                int(block["tag_id"]) for block in await db.fetch_all(
                    "SELECT tag_id FROM asset_tag_blocks WHERE asset_id = %s", (asset_id,),
                ) or []
            }
            asset_tags = sorted(direct | {
                name for tag_id, name in company_tag_ids.items() if tag_id not in blocked
            })

    source = agent or {}
    public_agent: dict[str, Any] = {key: None for key in _AGENT_FIELDS}
    public_agent.update({key: source.get(key) for key in ("hostname", "os", "os_version", "last_ip")})
    public_agent["architecture"] = None
    public_agent["version"] = None
    if asset is not None and source.get("os_version"):
        asset["os_version"] = source.get("os_version")
    return {
        "agent": public_agent, "asset": asset, "company": company,
        "software": None, "software_versions": None, "software_publishers": None,
        "asset_tags": asset_tags,
        "company_tags": sorted(set(company_tag_ids.values())),
        "asset_custom": asset_custom,
        "company_variables": company_variables,
        "incomplete_fields": [],
    }
