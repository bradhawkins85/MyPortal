"""Hudu API client service."""

from __future__ import annotations

from typing import Any

import httpx
from app.services.module_gate import require_module_enabled

from app.core.logging import log_error
from app.services import modules as modules_service


class HuduConfigurationError(Exception):
    """Raised when Hudu is not configured or credentials are missing."""


class HuduAuthenticationError(Exception):
    """Raised when Hudu rejects API authentication or authorization."""


class HuduDeviceSyncError(Exception):
    """Raised when a discovered device cannot safely be synchronized."""


_PASSWORD_ACCESS_MESSAGE = (
    "Hudu rejected the API key while accessing asset passwords. "
    "Confirm the configured Hudu API key is current, has Passwords access enabled, "
    "is allowed for this company/IP address, and is not Magic Dash only."
)


def _raise_for_status(
    response: httpx.Response, *, password_access: bool = False
) -> None:
    if response.status_code == 401:
        message = (
            _PASSWORD_ACCESS_MESSAGE if password_access else "Invalid Hudu API key"
        )
        raise HuduAuthenticationError(message)
    response.raise_for_status()


async def _load_settings() -> dict[str, Any]:
    """Load and validate Hudu module settings."""
    module = await modules_service.get_module("hudu", redact=False)
    if not module:
        raise HuduConfigurationError("Hudu module is not configured")
    if not module.get("enabled"):
        raise HuduConfigurationError("Hudu module is not enabled")

    raw_settings = module.get("settings") or {}
    if not isinstance(raw_settings, dict):
        raw_settings = {}

    base_url = str(raw_settings.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise HuduConfigurationError("Hudu base URL is not configured")

    api_key = str(raw_settings.get("api_key") or "").strip()
    if not api_key:
        raise HuduConfigurationError("Hudu API key is not configured")

    return {"base_url": base_url, "api_key": api_key}


def _make_headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def search_companies(name: str) -> list[dict[str, Any]]:
    """Search Hudu companies by name.

    Args:
        name: Company name to search for.

    Returns:
        List of matching company records from Hudu.
    """
    settings = await _load_settings()
    base_url = settings["base_url"]
    api_key = settings["api_key"]

    url = f"{base_url}/api/v1/companies"
    params = {"name": name}

    await require_module_enabled("hudu")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=_make_headers(api_key), params=params)
        _raise_for_status(response)

    data = response.json()
    companies = data.get("companies", [])
    return companies if isinstance(companies, list) else []


async def get_company_url(hudu_id: str) -> str | None:
    """Return the full Hudu URL for a company.

    Args:
        hudu_id: The Hudu company ID.

    Returns:
        Full URL to the Hudu company page, or None if not found or not configured.

    Raises:
        HuduConfigurationError: If Hudu is not configured (caller may choose to handle
            this differently from a lookup failure).
    """
    settings = await _load_settings()
    base_url = settings["base_url"]
    api_key = settings["api_key"]

    try:
        url = f"{base_url}/api/v1/companies/{hudu_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=_make_headers(api_key))
        if response.status_code == 404:
            return None
        _raise_for_status(response)
        data = response.json()
        company = data.get("company") or {}
        full_url = str(company.get("full_url") or "").strip()
        if full_url:
            return full_url
        slug = str(company.get("slug") or "").strip()
        if slug:
            return f"{base_url}/companies/{slug}"
        return f"{base_url}/companies/{hudu_id}"
    except httpx.HTTPError as exc:
        log_error("Failed to get Hudu company URL", hudu_id=hudu_id, error=str(exc))
        return None
    except Exception as exc:
        log_error(
            "Unexpected error getting Hudu company URL", hudu_id=hudu_id, error=str(exc)
        )
        return None


async def create_person(
    *,
    company_id: str,
    first_name: str,
    last_name: str,
    email: str | None = None,
    job_title: str | None = None,
    phone: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create a person (contact) in Hudu under a company.

    Args:
        company_id: The Hudu company ID to create the person under.
        first_name: First name of the person.
        last_name: Last name of the person.
        email: Email address of the person.
        job_title: Job title of the person.
        phone: Phone number of the person.
        notes: Additional notes.

    Returns:
        The created person record from Hudu.
    """
    settings = await _load_settings()
    base_url = settings["base_url"]
    api_key = settings["api_key"]

    url = f"{base_url}/api/v1/companies/{company_id}/people"
    person_payload: dict[str, Any] = {
        "first_name": first_name,
        "last_name": last_name,
    }
    if email:
        person_payload["email"] = email
    if job_title:
        person_payload["job_title"] = job_title
    if phone:
        person_payload["phone"] = phone
    if notes:
        person_payload["notes"] = notes

    body = {"person": person_payload}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=_make_headers(api_key), json=body)
        _raise_for_status(response)

    data = response.json()
    return data.get("person") or data


async def create_asset_password(
    *,
    company_id: str,
    name: str,
    password: str,
    username: str | None = None,
    url: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a password entry in Hudu under a company.

    Args:
        company_id: The Hudu company ID to create the password under.
        name: Label / name for the password entry.
        password: The secret password value.
        username: Optional associated username.
        url: Optional URL for the credential.
        description: Optional description.

    Returns:
        The created asset_password record from Hudu.
    """
    settings = await _load_settings()
    base_url = settings["base_url"]
    api_key = settings["api_key"]

    endpoint = f"{base_url}/api/v1/asset_passwords"
    pw_payload: dict[str, Any] = {
        "name": name,
        "company_id": company_id,
        "password": password,
    }
    if username:
        pw_payload["username"] = username
    if url:
        pw_payload["url"] = url
    if description:
        pw_payload["description"] = description

    body = {"asset_password": pw_payload}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            endpoint, headers=_make_headers(api_key), json=body
        )
        _raise_for_status(response, password_access=True)

    data = response.json()
    return data.get("asset_password") or data


async def get_base_url() -> str | None:
    """Return the configured Hudu base URL, or None if not configured."""
    try:
        settings = await _load_settings()
        return settings.get("base_url") or None
    except HuduConfigurationError:
        return None
    except Exception as exc:
        log_error("Failed to get Hudu base URL", error=str(exc))
        return None


_DEVICE_FIELDS = ("MAC Address", "Hostname", "Local IP", "Vendor", "First Seen")


def _records(data: Any, key: str) -> list[dict[str, Any]]:
    records = data.get(key, []) if isinstance(data, dict) else []
    return records if isinstance(records, list) else []


def _normalise_mac(value: Any) -> str:
    compact = "".join(c for c in str(value or "") if c.isalnum()).upper()
    if len(compact) == 12:
        return ":".join(compact[i : i + 2] for i in range(0, 12, 2))
    return compact


async def sync_discovered_device(
    *, company_id: str, device: dict[str, Any]
) -> dict[str, Any]:
    """Create or update a typed Hudu asset while preserving unmanaged fields."""
    device_type = str(device.get("device_type_name") or "").strip()
    mac_address = _normalise_mac(device.get("mac_address"))
    if not device_type:
        raise HuduDeviceSyncError("Set a device type before sending the device to Hudu")
    if not mac_address:
        raise HuduDeviceSyncError(
            "A MAC address is required to match the device in Hudu"
        )

    settings = await _load_settings()
    base_url, api_key = settings["base_url"], settings["api_key"]
    headers = _make_headers(api_key)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{base_url}/api/v1/asset_layouts",
            headers=headers,
            params={"page_size": 1000},
        )
        _raise_for_status(response)
        layout = next(
            (
                item
                for item in _records(response.json(), "asset_layouts")
                if str(item.get("name") or "").casefold() == device_type.casefold()
            ),
            None,
        )
        if layout is None:
            response = await client.post(
                f"{base_url}/api/v1/asset_layouts",
                headers=headers,
                json={
                    "asset_layout": {
                        "name": device_type,
                        "fields": [
                            {
                                "label": label,
                                "field_type": "Text",
                                "required": False,
                                "show_in_list": True,
                            }
                            for label in _DEVICE_FIELDS
                        ],
                    }
                },
            )
            _raise_for_status(response)
            data = response.json()
            layout = data.get("asset_layout") or data

        layout_id = layout.get("id")
        layout_fields = layout.get("fields") or layout.get("asset_layout_fields") or []
        field_ids = {
            str(field.get("label") or field.get("name") or "").casefold(): field.get(
                "id"
            )
            for field in layout_fields
            if isinstance(field, dict)
        }
        missing = [
            label for label in _DEVICE_FIELDS if not field_ids.get(label.casefold())
        ]
        if missing:
            raise HuduDeviceSyncError(
                f"The Hudu asset layout '{device_type}' is missing MyPortal fields: "
                + ", ".join(missing)
            )

        response = await client.get(
            f"{base_url}/api/v1/companies/{company_id}/assets",
            headers=headers,
            params={"asset_layout_id": layout_id, "page_size": 1000},
        )
        _raise_for_status(response)
        assets = _records(response.json(), "assets")

        def field_value(asset: dict[str, Any], field_id: Any) -> Any:
            for field in asset.get("fields") or []:
                current_id = field.get("asset_layout_field_id") or field.get("id")
                if str(current_id) == str(field_id):
                    return field.get("value")
            return None

        existing = next(
            (
                asset
                for asset in assets
                if _normalise_mac(field_value(asset, field_ids["mac address"]))
                == mac_address
            ),
            None,
        )
        first_seen = device.get("first_seen_at")
        values = {
            "mac address": mac_address,
            "hostname": str(device.get("hostname") or ""),
            "local ip": str(device.get("ip_address") or ""),
            "vendor": str(device.get("mac_vendor") or device.get("vendor") or ""),
            "first seen": first_seen.isoformat()
            if hasattr(first_seen, "isoformat")
            else str(first_seen or ""),
        }
        managed_ids = {str(field_ids[label]) for label in values}
        merged_fields = [
            {
                "asset_layout_field_id": field.get("asset_layout_field_id")
                or field.get("id"),
                "value": field.get("value"),
            }
            for field in (existing.get("fields") or [] if existing else [])
            if str(field.get("asset_layout_field_id") or field.get("id"))
            not in managed_ids
        ]
        merged_fields.extend(
            {"asset_layout_field_id": field_ids[label], "value": value}
            for label, value in values.items()
        )
        payload = {
            "asset": {
                "name": str(
                    device.get("hostname") or device.get("ip_address") or mac_address
                ),
                "asset_layout_id": layout_id,
                "company_id": company_id,
                "fields": merged_fields,
            }
        }
        if existing:
            response = await client.put(
                f"{base_url}/api/v1/assets/{existing['id']}",
                headers=headers,
                json=payload,
            )
            action = "updated"
        else:
            response = await client.post(
                f"{base_url}/api/v1/companies/{company_id}/assets",
                headers=headers,
                json=payload,
            )
            action = "created"
        _raise_for_status(response)
        data = response.json()
        return {"action": action, "asset": data.get("asset") or data}
