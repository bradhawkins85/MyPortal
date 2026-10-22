"""RMM device inventory: hardware and software snapshots sent by the RMM agent.

The agent sends a snapshot when its service starts and whenever
``GET /api/rmm/agent/inventory/config`` says one is due (:func:`agent_config`):
there is none yet, the last is older than :data:`REFRESH_INTERVAL`, or a
technician pressed **Refresh** on the asset page (:func:`request_refresh`).
:func:`ingest` keeps the latest snapshot per agent in the main database.

Snapshots are also there to pick devices. A condition is
``{"field", "operator", "value"}`` on one of :data:`FIELDS`, for example
``{"field": "ram_gb", "operator": "at_least", "value": "16"}`` or
``{"field": "software", "operator": "not_installed", "value": "Google Chrome"}``.
:func:`matches` checks a device against a list of them (every condition must
match) and :func:`filter_agents` narrows a list of agents, so schedules,
onboarding steps, rules and checks can add inventory filters next to their tag
filters. Devices that have not sent a snapshot match no condition.
"""

from __future__ import annotations

import fnmatch
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from app.core.logging import log_info, log_warning
from app.repositories import rmm_inventory as repo
from app.repositories import network_map as network_map_repo

REFRESH_INTERVAL = timedelta(hours=24)
POLL_SECONDS = 120
MAX_SOFTWARE = 5000
GIB = 1024 ** 3

FIELDS: dict[str, dict[str, Any]] = {
    "ram_gb": {"label": "RAM (GB)", "type": "number"},
    "cpu_cores": {"label": "CPU cores", "type": "number"},
    "cpu_logical_processors": {"label": "Logical processors", "type": "number"},
    "disk_total_gb": {"label": "Total disk (GB)", "type": "number"},
    "system_disk_free_gb": {"label": "System disk free (GB)", "type": "number"},
    "uptime_days": {"label": "Days since boot", "type": "number"},
    "os_name": {"label": "Operating system", "type": "text"},
    "os_version": {"label": "OS version", "type": "version"},
    "os_build": {"label": "OS build", "type": "version"},
    "manufacturer": {"label": "Manufacturer", "type": "text"},
    "model": {"label": "Model", "type": "text"},
    "cpu_model": {"label": "CPU", "type": "text"},
    "domain": {"label": "Domain", "type": "text"},
    "arch": {"label": "Architecture", "type": "text"},
    "software": {"label": "Software", "type": "software"},
}
OPERATORS = {
    "at_least": "is at least",
    "at_most": "is at most",
    "below": "is below",
    "above": "is above",
    "is": "is",
    "is_not": "is not",
    "contains": "contains",
    "not_contains": "does not contain",
    "starts_with": "starts with",
    "installed": "is installed",
    "not_installed": "is not installed",
    "version_below": "is installed below version",
    "version_at_least": "is installed at version or above",
}
TYPE_OPERATORS = {
    "number": ("at_least", "at_most", "below", "above", "is", "is_not"),
    "version": ("is", "is_not", "starts_with", "at_least", "below"),
    "text": ("is", "is_not", "contains", "not_contains", "starts_with"),
    "software": ("installed", "not_installed", "version_below", "version_at_least"),
}
MAX_CONDITIONS = 10
MAX_VALUE_LENGTH = 255


class InventoryConditionError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_ISO_TIME = re.compile(r"^(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$")


def _as_datetime(value: Any) -> datetime | None:
    """Naive UTC from a datetime or an ISO 8601 string. The agent sends Go's
    RFC 3339 times, whose fractions can have up to nine digits, which Python
    3.10's ``fromisoformat`` does not read."""

    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if not isinstance(value, str) or not value:
        return None
    match = _ISO_TIME.match(value.strip())
    if not match:
        return None
    base, fraction, zone = match.groups()
    text = base + "." + (fraction or "0")[:6].ljust(6, "0") + ("+00:00" if zone in (None, "Z") else zone)
    try:
        return _as_datetime(datetime.fromisoformat(text))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Agent side
# --------------------------------------------------------------------------- #


def snapshot_due(inventory: Mapping[str, Any] | None, now: datetime | None = None) -> bool:
    now = now or _utcnow()
    if not inventory:
        return True
    received = _as_datetime(inventory.get("received_at"))
    if received is None or now - received >= REFRESH_INTERVAL:
        return True
    requested = _as_datetime(inventory.get("refresh_requested_at"))
    return requested is not None and requested > received


async def agent_config(agent: Mapping[str, Any]) -> dict[str, Any]:
    inventory = await repo.get_for_agent(int(agent["id"]))
    return {"due": snapshot_due(inventory), "poll_seconds": POLL_SECONDS}


def _text(value: Any, limit: int = 255) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\x00", "").strip()
    return text[:limit] or None


def _int(value: Any, high: int = 2 ** 62) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number <= high else None


def _install_date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value or "")[:10]).isoformat()
    except ValueError:
        return None


def normalise(payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Split an agent's snapshot into summary columns, details and software rows."""

    disks = [
        {
            "name": _text(disk.get("name"), 128) or "",
            "label": _text(disk.get("label"), 128),
            "file_system": _text(disk.get("file_system"), 32),
            "size_bytes": _int(disk.get("size_bytes")) or 0,
            "free_bytes": max(0, int(disk.get("free_bytes") or 0)),
            "system": bool(disk.get("system")),
        }
        for disk in payload.get("disks") or [] if isinstance(disk, Mapping)
    ]
    system_disk = next((disk for disk in disks if disk["system"]), None)
    summary = {
        "hostname": _text(payload.get("hostname")),
        "manufacturer": _text(payload.get("manufacturer")),
        "model": _text(payload.get("model")),
        "serial_number": _text(payload.get("serial_number")),
        "bios_version": _text(payload.get("bios_version")),
        "os_name": _text(payload.get("os_name")),
        "os_version": _text(payload.get("os_version"), 128),
        "os_build": _text(payload.get("os_build"), 128),
        "arch": _text(payload.get("arch"), 32),
        "domain": _text(payload.get("domain")),
        "cpu_model": _text(payload.get("cpu_model")),
        "cpu_cores": _int(payload.get("cpu_cores"), 100_000),
        "cpu_logical_processors": _int(payload.get("cpu_logical_processors"), 100_000),
        "ram_bytes": _int(payload.get("ram_bytes")),
        "disk_total_bytes": sum(disk["size_bytes"] for disk in disks) or None,
        "system_disk_free_bytes": system_disk["free_bytes"] if system_disk else None,
        "last_boot_at": _as_datetime(payload.get("last_boot_at")),
    }
    details = {
        "disks": disks,
        "physical_disks": [
            {key: item.get(key) for key in ("model", "size_bytes", "media_type", "bus_type", "serial")}
            for item in payload.get("physical_disks") or [] if isinstance(item, Mapping)
        ],
        "memory_modules": [
            {key: item.get(key) for key in ("slot", "size_bytes", "speed_mhz", "manufacturer", "part_number")}
            for item in payload.get("memory_modules") or [] if isinstance(item, Mapping)
        ],
        "gpus": [_text(gpu) for gpu in payload.get("gpus") or [] if _text(gpu)],
        "network_adapters": [
            {
                "name": _text(item.get("name")) or "",
                "mac": _text(item.get("mac"), 64),
                "addresses": [str(address)[:64] for address in item.get("addresses") or []][:16],
            }
            for item in payload.get("network_adapters") or [] if isinstance(item, Mapping)
        ],
        "errors": [str(error)[:500] for error in payload.get("errors") or []][:20],
    }
    software = []
    seen: set[tuple[str, str]] = set()
    for item in payload.get("software") or []:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("name"))
        version = _text(item.get("version"), 128)
        if not name or (name.casefold(), version or "") in seen:
            continue
        seen.add((name.casefold(), version or ""))
        software.append({
            "name": name, "version": version, "publisher": _text(item.get("publisher")),
            "install_date": _install_date(item.get("install_date")), "source": _text(item.get("source"), 32),
        })
        if len(software) >= MAX_SOFTWARE:
            break
    return summary, details, software


async def ingest(agent: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    summary, details, software = normalise(payload)
    collected_at = _as_datetime(payload.get("collected_at")) or _utcnow()
    # A device clock in the future must not make the snapshot look fresh forever.
    collected_at = min(collected_at, _utcnow())
    await repo.save_snapshot(dict(agent), summary, details, software, collected_at=collected_at)
    if agent.get("company_id") is not None and agent.get("asset_id") is not None:
        await network_map_repo.sync_agent_interfaces(
            int(agent["company_id"]), int(agent["asset_id"]), details["network_adapters"],
        )
    log_info("RMM inventory received", agent_id=agent.get("id"), software=len(software))
    # Resume only after hardware, software and network interfaces have been saved.
    # The periodic reconciler retries if onboarding cannot advance right now.
    from app.services import rmm_automation

    try:
        await rmm_automation.reconcile_onboarding(agent_id=int(agent["id"]))
    except Exception as exc:  # noqa: BLE001 - inventory has already been saved
        log_warning("RMM onboarding could not resume after inventory", agent_id=agent["id"], error=str(exc))
    return {"ok": True, "software": len(software)}


async def request_refresh(agent: Mapping[str, Any], user_id: int | None) -> None:
    await repo.request_refresh(dict(agent), user_id)


# --------------------------------------------------------------------------- #
# Display
# --------------------------------------------------------------------------- #


def format_bytes(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") or size >= 100 else f"{size:.1f} {unit}"
        size /= 1024
    return ""


async def asset_inventory_context(agent: Mapping[str, Any] | None, *, can_refresh: bool) -> dict[str, Any] | None:
    """The asset's Hardware and Software pages, or None without an agent."""

    if not agent:
        return None
    inventory = await repo.get_for_agent(int(agent["id"]))
    software = await repo.list_software(int(agent["id"])) if inventory and inventory.get("received_at") else []
    received = _as_datetime(inventory.get("received_at")) if inventory else None
    requested = _as_datetime(inventory.get("refresh_requested_at")) if inventory else None
    if inventory:
        inventory = {key: value.isoformat() if isinstance(value, (datetime, date)) else value
                     for key, value in inventory.items()}
    software = [{key: value.isoformat() if isinstance(value, date) else value for key, value in item.items()}
                for item in software]
    history = await repo.list_software_history(int(agent["id"])) if received else []
    apps: dict[tuple[str, str], dict[str, Any]] = {}
    for item in software:
        key = (item["name"].casefold(), item.get("source") or "")
        if key not in apps:
            apps[key] = {**item, "installed": True, "history": []}
        elif item.get("version"):
            apps[key]["version"] = ", ".join(filter(None, (apps[key].get("version"), item["version"])))
    for row in history:
        row = {key: value.isoformat() if isinstance(value, date) else value for key, value in row.items()}
        key = (row["name"].casefold(), row.get("source") or "")
        if key not in apps:
            apps[key] = {**row, "installed": False, "history": []}
        apps[key]["history"].append(row)
    return {
        "inventory": inventory if received else None,
        "software": software,
        "software_apps": sorted(apps.values(), key=lambda item: item["name"].casefold()),
        "refresh_pending": requested is not None and (received is None or requested > received),
        "can_refresh": can_refresh,
        # Chocolatey installs run through patch jobs, which need an active Windows agent.
        "can_install_apps": can_refresh and agent.get("status") == "active"
        and str(agent.get("os") or "").lower() == "windows",
        "format_bytes": format_bytes,
    }


# --------------------------------------------------------------------------- #
# Filtering devices
# --------------------------------------------------------------------------- #


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value or "")[:8])


def _compare_versions(actual: str, wanted: str) -> int:
    left, right = _version_key(actual), _version_key(wanted)
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return (left > right) - (left < right)


def _field_value(field: str, inventory: Mapping[str, Any], now: datetime) -> Any:
    if field == "ram_gb":
        return (inventory.get("ram_bytes") or 0) / GIB if inventory.get("ram_bytes") else None
    if field == "disk_total_gb":
        return (inventory.get("disk_total_bytes") or 0) / GIB if inventory.get("disk_total_bytes") else None
    if field == "system_disk_free_gb":
        free = inventory.get("system_disk_free_bytes")
        return free / GIB if free is not None else None
    if field == "uptime_days":
        boot = _as_datetime(inventory.get("last_boot_at"))
        return (now - boot).total_seconds() / 86400 if boot else None
    return inventory.get(field)


def _name_matches(pattern: str, name: str) -> bool:
    """Software names match case-insensitively: with * or ? as a wildcard
    pattern, otherwise when the name contains the text."""

    pattern, name = pattern.casefold(), name.casefold()
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatchcase(name, pattern)
    return pattern in name


def condition_matches(condition: Mapping[str, Any], inventory: Mapping[str, Any] | None,
                      software: Iterable[Mapping[str, Any]] = (), *, now: datetime | None = None) -> bool:
    """True when the device's snapshot meets the condition. A device without a
    snapshot matches nothing, not even "is not installed"."""

    if not inventory or not inventory.get("received_at"):
        return False
    field = str(condition.get("field") or "")
    operator = str(condition.get("operator") or "")
    value = str(condition.get("value") or "").strip()
    kind = FIELDS.get(field, {}).get("type")
    if kind is None or operator not in TYPE_OPERATORS[kind]:
        return False
    if kind == "software":
        if operator in ("version_below", "version_at_least"):
            name, _, version = value.rpartition(" ")
            if not name or not _version_key(version):
                return False
            installed = [item for item in software if _name_matches(name, str(item.get("name") or ""))]
            if not installed:
                return False
            compare = [_compare_versions(str(item.get("version") or ""), version) for item in installed]
            return any(c < 0 for c in compare) if operator == "version_below" else all(c >= 0 for c in compare)
        found = any(_name_matches(value, str(item.get("name") or "")) for item in software)
        return found == (operator == "installed")
    actual = _field_value(field, inventory, now or _utcnow())
    if actual is None:
        return False
    if kind == "number":
        try:
            wanted = float(value)
        except ValueError:
            return False
        number = float(actual)
        if field in ("ram_gb", "disk_total_gb"):
            # Reported memory is a little under the installed size (16 GB shows as 15.8).
            number = round(number)
        return {
            "at_least": number >= wanted, "at_most": number <= wanted, "below": number < wanted,
            "above": number > wanted, "is": number == wanted, "is_not": number != wanted,
        }[operator]
    text = str(actual).casefold()
    wanted_text = value.casefold()
    if kind == "version" and operator in ("at_least", "below"):
        if not _version_key(wanted_text) or not _version_key(text):
            return False
        result = _compare_versions(text, wanted_text)
        return result >= 0 if operator == "at_least" else result < 0
    return {
        "is": text == wanted_text, "is_not": text != wanted_text, "contains": wanted_text in text,
        "not_contains": wanted_text not in text, "starts_with": text.startswith(wanted_text),
    }[operator]


def matches(conditions: Iterable[Mapping[str, Any]], inventory: Mapping[str, Any] | None,
            software: Iterable[Mapping[str, Any]] = (), *, now: datetime | None = None) -> bool:
    """Every condition matches. An empty list matches every device."""

    software = list(software)
    return all(condition_matches(condition, inventory, software, now=now) for condition in conditions)


async def filter_agents(agents: list[dict[str, Any]], conditions: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The agents whose latest snapshot meets every condition."""

    if not conditions:
        return list(agents)
    ids = [int(agent["id"]) for agent in agents]
    inventories = await repo.list_for_agents(ids)
    needs_software = any(FIELDS.get(str(c.get("field")), {}).get("type") == "software" for c in conditions)
    software = await repo.list_software_for_agents(ids) if needs_software else {}
    now = _utcnow()
    return [
        agent for agent in agents
        if matches(conditions, inventories.get(int(agent["id"])), software.get(int(agent["id"]), []), now=now)
    ]


def describe_condition(condition: Mapping[str, Any]) -> str:
    field = FIELDS.get(str(condition.get("field")), {}).get("label", condition.get("field"))
    operator = OPERATORS.get(str(condition.get("operator")), condition.get("operator"))
    if condition.get("field") == "software":
        value = str(condition.get("value") or "")
        if condition.get("operator") in ("version_below", "version_at_least"):
            name, _, version = value.rpartition(" ")
            return f"{name} {operator} {version}"
        return f"{value} {operator}"
    return f"{field} {operator} {condition.get('value')}"


def parse_conditions(fields: list[Any], operators: list[Any], values: list[Any]) -> list[dict[str, str]]:
    """Check form rows (parallel lists); blank rows are skipped."""

    conditions: list[dict[str, str]] = []
    errors: dict[str, str] = {}
    for index, (field, operator, value) in enumerate(zip(fields, operators, values), start=1):
        field, operator, value = str(field or ""), str(operator or ""), str(value or "").strip()
        if not value:
            continue
        kind = FIELDS.get(field, {}).get("type")
        if kind is None:
            errors[f"inventory_{index}"] = "Choose what to check."
            continue
        if operator not in TYPE_OPERATORS[kind]:
            errors[f"inventory_{index}"] = f"{FIELDS[field]['label']} cannot be checked with “{OPERATORS.get(operator, operator)}”."
            continue
        if len(value) > MAX_VALUE_LENGTH:
            errors[f"inventory_{index}"] = "The value is too long."
            continue
        if kind == "number":
            try:
                float(value)
            except ValueError:
                errors[f"inventory_{index}"] = f"{FIELDS[field]['label']} needs a number."
                continue
        if operator in ("version_below", "version_at_least"):
            name, _, version = value.rpartition(" ")
            if not name.strip() or not _version_key(version):
                errors[f"inventory_{index}"] = "Enter the program's name then the version, such as “Google Chrome 120”."
                continue
        conditions.append({"field": field, "operator": operator, "value": value})
    if len(conditions) > MAX_CONDITIONS:
        errors["inventory"] = f"Use at most {MAX_CONDITIONS} inventory conditions."
    if errors:
        raise InventoryConditionError(errors)
    return conditions
