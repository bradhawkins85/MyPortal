"""Hudu API client service."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import log_error, log_info
from app.repositories import integration_modules as module_repo


class HuduConfigurationError(Exception):
    """Raised when Hudu is not configured or credentials are missing."""


async def _load_settings() -> dict[str, Any]:
    """Load and validate Hudu module settings."""
    module = await module_repo.get_module("hudu")
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

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=_make_headers(api_key), params=params)
        response.raise_for_status()

    data = response.json()
    companies = data.get("companies", [])
    return companies if isinstance(companies, list) else []


async def get_company_url(hudu_id: str) -> str | None:
    """Return the full Hudu URL for a company.

    Args:
        hudu_id: The Hudu company ID.

    Returns:
        Full URL to the Hudu company page, or None if not found.
    """
    try:
        settings = await _load_settings()
        base_url = settings["base_url"]
        api_key = settings["api_key"]

        url = f"{base_url}/api/v1/companies/{hudu_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=_make_headers(api_key))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        company = data.get("company") or {}
        full_url = str(company.get("full_url") or "").strip()
        if full_url:
            return full_url
        slug = str(company.get("slug") or "").strip()
        if slug:
            return f"{base_url}/companies/{slug}"
        return f"{base_url}/companies/{hudu_id}"
    except HuduConfigurationError:
        return None
    except Exception as exc:
        log_error("Failed to get Hudu company URL", hudu_id=hudu_id, error=str(exc))
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
        response.raise_for_status()

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

    endpoint = f"{base_url}/api/v1/companies/{company_id}/asset_passwords"
    pw_payload: dict[str, Any] = {
        "name": name,
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
        response = await client.post(endpoint, headers=_make_headers(api_key), json=body)
        response.raise_for_status()

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
