from __future__ import annotations

import asyncio
import json
import re
from time import monotonic
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from app.core.logging import log_error, log_info
from app.repositories import integration_modules as module_repo
from app.services import modules as modules_service
from app.services.modules import _coerce_settings


class TacticalRMMConfigurationError(RuntimeError):
    """Raised when the Tactical RMM module is missing or misconfigured."""


class TacticalRMMAPIError(RuntimeError):
    """Raised when Tactical RMM responds with an error payload."""


_MODULE_SETTINGS_CACHE: dict[str, Any] | None = None
_MODULE_SETTINGS_EXPIRY: float = 0.0
_MODULE_SETTINGS_LOCK = asyncio.Lock()


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        text = str(value)
        return text.strip() or None
    return None


async def _load_settings() -> dict[str, Any]:
    global _MODULE_SETTINGS_CACHE, _MODULE_SETTINGS_EXPIRY
    now = monotonic()
    if _MODULE_SETTINGS_CACHE and now < _MODULE_SETTINGS_EXPIRY:
        return _MODULE_SETTINGS_CACHE
    async with _MODULE_SETTINGS_LOCK:
        now = monotonic()
        if _MODULE_SETTINGS_CACHE and now < _MODULE_SETTINGS_EXPIRY:
            return _MODULE_SETTINGS_CACHE
        module = await module_repo.get_module("tacticalrmm")
        if not module:
            raise TacticalRMMConfigurationError("Tactical RMM module is not configured")
        raw_settings: Mapping[str, Any] | None
        if isinstance(module.get("settings"), Mapping):
            raw_settings = module["settings"]  # type: ignore[index]
        else:
            raw_settings = None
            if isinstance(module.get("settings"), str):
                try:
                    raw_settings = json.loads(module["settings"])  # type: ignore[index]
                except json.JSONDecodeError:
                    raw_settings = None
        settings = _coerce_settings("tacticalrmm", raw_settings, module)
        base_url = _clean_text(settings.get("base_url"))
        api_key = _clean_text(settings.get("api_key"))
        if not base_url:
            raise TacticalRMMConfigurationError("Tactical RMM base URL is not configured")
        if not api_key:
            raise TacticalRMMConfigurationError("Tactical RMM API key is not configured")
        verify_ssl = bool(settings.get("verify_ssl", True))
        cached = {
            "base_url": base_url.rstrip("/"),
            "api_key": api_key,
            "verify_ssl": verify_ssl,
        }
        _MODULE_SETTINGS_CACHE = cached
        _MODULE_SETTINGS_EXPIRY = now + 30.0
        return cached


async def _call_endpoint(endpoint: str) -> Any:
    payload = {"endpoint": endpoint, "method": "GET"}
    try:
        result = await modules_service.trigger_module("tacticalrmm", payload, background=False)
    except ValueError as exc:
        raise TacticalRMMConfigurationError(str(exc)) from exc
    status = str(result.get("status") or "").lower()
    if status == "skipped":
        reason = result.get("reason") or "Tactical RMM module is disabled"
        raise TacticalRMMConfigurationError(str(reason))
    if status not in {"succeeded", "ok"}:
        error_message = (
            result.get("error")
            or result.get("last_error")
            or result.get("reason")
            or "Tactical RMM request failed"
        )
        raise TacticalRMMAPIError(str(error_message))
    return result.get("response")


def _normalise_next_url(next_value: Any, base_url: str) -> str | None:
    if not next_value:
        return None
    if isinstance(next_value, Mapping):
        for key in ("url", "next", "next_url", "href"):
            candidate = next_value.get(key)
            if candidate:
                next_value = candidate
                break
    if isinstance(next_value, Sequence) and not isinstance(next_value, (str, bytes, bytearray)):
        for candidate in next_value:
            resolved = _normalise_next_url(candidate, base_url)
            if resolved:
                return resolved
        return None
    if not isinstance(next_value, str):
        next_value = str(next_value)
    if not next_value:
        return None
    if next_value.startswith("http://") or next_value.startswith("https://"):
        parsed = urlparse(next_value)
        path = parsed.path.lstrip("/")
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{path}{query}" if path or query else None
    return next_value.lstrip("/")


def _extract_agent_page(response: Any, base_url: str) -> tuple[list[Mapping[str, Any]], str | None]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, Mapping)], None
    if isinstance(response, Mapping):
        for key in ("results", "agents", "items", "data"):
            value = response.get(key)
            if isinstance(value, list):
                next_link = response.get("next") or response.get("next_url") or response.get("nextLink")
                if not next_link:
                    pagination = response.get("links") or response.get("pagination")
                    if isinstance(pagination, Mapping):
                        next_link = pagination.get("next") or pagination.get("next_url")
                next_endpoint = _normalise_next_url(next_link, base_url)
                return [item for item in value if isinstance(item, Mapping)], next_endpoint
        # Some endpoints may return a single agent
        if all(key in response for key in ("id", "hostname", "client")):
            return [response], None
    return [], None


def _coerce_ram_gb(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric >= 1024:
            numeric = numeric / 1024.0
        return round(numeric, 2)
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return None
    number = float(match.group(1))
    lowered = text.lower()
    if "mb" in lowered and "gb" not in lowered:
        number = number / 1024.0
    return round(number, 2)


def _ram_gb_from_wmi_memory(memory: Any) -> float | None:
    """Compute total RAM in GB by summing Capacity (bytes) from wmi_detail memory modules.

    TacticalRMM stores per-module memory data as a list of lists of dicts, e.g.:
    ``[[{"Capacity": "8589934592"}, ...], [{"Capacity": "8589934592"}, ...]]``
    Each ``Capacity`` value is in bytes.

    Returns the total installed RAM in GB rounded to 2 decimal places, or
    ``None`` if the structure is absent, empty, or contains no valid Capacity
    values.
    """
    if not isinstance(memory, list):
        return None
    total_bytes = 0
    for module in memory:
        if not isinstance(module, list):
            continue
        for entry in module:
            if not isinstance(entry, dict):
                continue
            raw = entry.get("Capacity")
            if raw is None:
                continue
            try:
                total_bytes += int(raw)
            except (TypeError, ValueError):
                continue
    if total_bytes <= 0:
        return None
    return round(total_bytes / (1024 ** 3), 2)


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None


def _join_list(value: Any, separator: str = ", ") -> str | None:
    """Convert a list to a delimited string; pass through non-list values unchanged."""
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if item is not None and str(item).strip()]
        return separator.join(parts) if parts else None
    if isinstance(value, str):
        return value.strip() or None
    return str(value).strip() or None


def extract_agent_details(agent: Mapping[str, Any]) -> dict[str, Any]:
    # Support the TacticalRMM native ``wmi_detail`` sub-object as well as a
    # generic ``hardware`` sub-object that third-party proxies may expose.
    wmi_detail: Mapping[str, Any] = (
        agent.get("wmi_detail") if isinstance(agent.get("wmi_detail"), Mapping) else {}
    )
    generic_hw: Mapping[str, Any] = (
        agent.get("hardware") if isinstance(agent.get("hardware"), Mapping) else {}
    )
    hardware: Mapping[str, Any] = wmi_detail if wmi_detail else generic_hw

    def _lookup(source: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in source and source[key] not in (None, ""):
                return source[key]
        return None

    name = _clean_text(
        _lookup(
            agent,
            "agent_name",
            "hostname",
            "name",
            "computername",
        )
    )
    client_info = agent.get("client") if isinstance(agent.get("client"), Mapping) else {}
    site_info = agent.get("site") if isinstance(agent.get("site"), Mapping) else {}

    # ``logged_username`` is the computed field name in AgentTableSerializer;
    # ``logged_in_username`` is the underlying model field.  Either may carry
    # the sentinel value "-" or the string "None" when no user is active.
    raw_last_user = _lookup(
        agent,
        "logged_in_username",
        "logged_username",
        "logged_in_user",
        "last_logged_in_user",
        "current_user",
    )
    if raw_last_user in ("-", "None", "N/A"):
        raw_last_user = None

    details = {
        "name": name or "Agent",
        "type": _clean_text(_lookup(agent, "monitoring_type", "agent_type", "type")),
        "serial_number": _clean_text(
            _lookup(agent, "serial_number", "serial", "bios_serial")
            or _lookup(hardware, "serial", "serial_number")
        ),
        "status": _clean_text(_lookup(agent, "status", "agent_status", "monitoring_status")),
        "os_name": _clean_text(_lookup(agent, "os", "operating_system", "os_name", "os_version")),
        # cpu_model is a list in the real TacticalRMM API – join multiple CPUs
        "cpu_name": _clean_text(
            _join_list(_lookup(agent, "cpu_model", "processor"))
            or _join_list(_lookup(hardware, "cpu_model", "cpu", "processor"))
        ),
        "ram_gb": (
            _coerce_ram_gb(
                _lookup(agent, "ram_gb", "total_ram", "ram")
                or _lookup(hardware, "ram", "total_ram")
            )
            or _ram_gb_from_wmi_memory(_lookup(hardware, "memory"))
        ),
        # physical_disks is a list in the real TacticalRMM API – join entries
        "hdd_size": _clean_text(
            _lookup(agent, "total_disk", "hdd_size")
            or _join_list(_lookup(agent, "physical_disks", "disks"), separator=" | ")
            or _lookup(hardware, "total_disk", "storage_total", "disk")
        ),
        "last_sync": _lookup(
            agent,
            "last_seen",
            "last_checkin",
            "checkin_time",
            "last_sync",
            "last_agent_checkin",
        ),
        "motherboard_manufacturer": _clean_text(
            _lookup(hardware, "motherboard_manufacturer", "board_manufacturer")
        ),
        "form_factor": _clean_text(
            _lookup(agent, "chassis", "form_factor")
            or _lookup(hardware, "chassis_type", "form_factor")
        ),
        "last_user": _clean_text(raw_last_user),
        "approx_age": _coerce_float(
            _lookup(hardware, "system_age_years", "age_years")
            or _lookup(agent, "system_age", "device_age")
        ),
        "performance_score": _coerce_float(
            _lookup(agent, "performance_score")
            or _lookup(hardware, "performance_score")
        ),
        "warranty_status": _clean_text(_lookup(agent, "warranty_status")),
        "warranty_end_date": _lookup(agent, "warranty_expires", "warranty_end", "warranty_expiration"),
        "tactical_asset_id": _clean_text(
            _lookup(agent, "agent_id", "id", "pk")
        ),
        "client_id": _clean_text(
            _lookup(client_info, "id", "pk", "client_id")
            or _lookup(agent, "client_id", "client")
        ),
        "client_name": _clean_text(
            _lookup(client_info, "name", "client")
            or _lookup(agent, "client_name", "client")
        ),
        "site_name": _clean_text(
            _lookup(site_info, "name", "site")
            or _lookup(agent, "site", "site_name")
        ),
    }
    if not details["tactical_asset_id"] and details.get("client_id") and details.get("name"):
        details["tactical_asset_id"] = f"{details['client_id']}::{details['name']}"
    return details


async def _fetch_agent_detail(agent_id: str) -> Mapping[str, Any] | None:
    """Fetch full agent details from the per-agent endpoint.

    The list endpoint (``/agents/``) uses ``AgentTableSerializer`` which omits
    ``total_ram``.  The detail endpoint (``/agents/{agent_id}/``) uses
    ``AgentSerializer`` with ``exclude = ["id"]``, so it includes ``total_ram``
    (stored in MB) and ``wmi_detail`` which ``extract_agent_details`` can use.
    """
    try:
        result = await _call_endpoint(f"agents/{agent_id}/")
        if isinstance(result, Mapping):
            return result
    except (TacticalRMMAPIError, TacticalRMMConfigurationError) as exc:
        log_error(
            "Failed to fetch Tactical RMM agent detail",
            agent_id=agent_id,
            error=str(exc),
        )
    return None


async def fetch_agents(client_id: str | None = None) -> list[Mapping[str, Any]]:
    settings = await _load_settings()
    base_url = settings["base_url"]
    endpoints: list[str]
    if client_id:
        endpoints = [
            f"agents/?client={client_id}",
        ]
    else:
        endpoints = ["agents/"]

    collected: list[Mapping[str, Any]] = []
    log_info("Fetching Tactical RMM agents", client_id=client_id)
    for endpoint in endpoints:
        try:
            response = await _call_endpoint(endpoint)
        except TacticalRMMAPIError as exc:
            log_error("Failed to fetch Tactical RMM agents", endpoint=endpoint, error=str(exc))
            continue
        page_items, next_endpoint = _extract_agent_page(response, base_url)
        if page_items:
            collected.extend(page_items)
        seen_endpoints: set[str] = set()
        while next_endpoint and next_endpoint not in seen_endpoints:
            seen_endpoints.add(next_endpoint)
            try:
                response = await _call_endpoint(next_endpoint)
            except TacticalRMMAPIError as exc:
                log_error("Failed to fetch Tactical RMM agents page", endpoint=next_endpoint, error=str(exc))
                break
            page_items, next_endpoint = _extract_agent_page(response, base_url)
            if page_items:
                collected.extend(page_items)
        if collected:
            break

    # The list endpoint (AgentTableSerializer) omits ``total_ram`` and
    # ``wmi_detail``.  Enrich each agent with its full detail record so that
    # RAM data is available for extract_agent_details().
    if collected:
        agent_ids = [
            str(a.get("agent_id") or a.get("id") or "").strip()
            for a in collected
        ]
        details: list[Mapping[str, Any] | None] = list(
            await asyncio.gather(
                *[_fetch_agent_detail(aid) for aid in agent_ids if aid],
                return_exceptions=True,
            )
        )
        detail_map: dict[str, Mapping[str, Any]] = {}
        for detail in details:
            if not isinstance(detail, Mapping):
                continue
            did = str(detail.get("agent_id") or detail.get("id") or "").strip()
            if did:
                detail_map[did] = detail

        enriched: list[Mapping[str, Any]] = []
        for agent in collected:
            aid = str(agent.get("agent_id") or agent.get("id") or "").strip()
            if aid and aid in detail_map:
                merged: dict[str, Any] = dict(agent)
                merged.update(detail_map[aid])
                enriched.append(merged)
            else:
                enriched.append(agent)
        return enriched

    return collected


def extract_trmm_custom_fields(agent: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse custom fields from a TRMM agent response into a name-keyed dict.

    Each entry in the returned dict has:
    - ``type``: the TRMM field type (e.g. "checkbox", "text", "number")
    - ``value``: the resolved value (bool for checkbox, str for others)
    """
    raw = agent.get("custom_fields")
    if not isinstance(raw, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for field in raw:
        if not isinstance(field, Mapping):
            continue
        name = _clean_text(field.get("name"))
        if not name:
            continue
        field_type = _clean_text(field.get("type")) or "text"
        if field_type == "checkbox":
            value: Any = field.get("bool_value")
            if value is None:
                raw_bool = field.get("value")
                if isinstance(raw_bool, bool):
                    value = raw_bool
                elif raw_bool is not None:
                    value = str(raw_bool).lower() in ("true", "1", "yes")
        else:
            value = field.get("string_value")
            if value is None:
                value = field.get("value")
        result[name] = {"type": field_type, "value": value}
    return result


async def fetch_agent_installed_software(agent_id: str) -> list[str]:
    """Return a list of installed software names for a TRMM agent.

    Calls the ``software/{agent_id}/`` endpoint.  Returns an empty list on
    error so that callers can degrade gracefully.
    """
    try:
        result = await _call_endpoint(f"software/{agent_id}/")
    except (TacticalRMMAPIError, TacticalRMMConfigurationError) as exc:
        log_error(
            "Failed to fetch installed software from Tactical RMM",
            agent_id=agent_id,
            error=str(exc),
        )
        return []
    names: list[str] = []
    items: list[Any] = []
    if isinstance(result, list):
        items = result
    elif isinstance(result, Mapping):
        for key in ("results", "software", "items", "data"):
            candidate = result.get(key)
            if isinstance(candidate, list):
                items = candidate
                break
    for item in items:
        if isinstance(item, Mapping):
            name = _clean_text(item.get("name"))
            if name:
                names.append(name)
    return names


async def fetch_clients() -> list[Mapping[str, Any]]:
    """
    Fetch all Tactical RMM clients from the /beta/v1/client/ endpoint.
    
    Returns:
        List of client dictionaries with 'id' and 'name' fields
    """
    await _load_settings()
    endpoint = "beta/v1/client/"
    
    collected: list[Mapping[str, Any]] = []
    log_info("Fetching Tactical RMM clients")
    
    try:
        response = await _call_endpoint(endpoint)
    except TacticalRMMAPIError as exc:
        log_error("Failed to fetch Tactical RMM clients", endpoint=endpoint, error=str(exc))
        return collected
    
    # The endpoint returns a list of client objects
    if isinstance(response, list):
        for item in response:
            if isinstance(item, Mapping):
                collected.append(item)
    elif isinstance(response, Mapping):
        # Handle paginated response with 'results' key
        results = response.get("results")
        if isinstance(results, list):
            for item in results:
                if isinstance(item, Mapping):
                    collected.append(item)
        # Handle case where response is a single client
        elif "id" in response and "name" in response:
            collected.append(response)
    
    return collected
