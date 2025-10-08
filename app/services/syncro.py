from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import log_error, log_info


class SyncroConfigurationError(RuntimeError):
    """Raised when Syncro integration settings are incomplete."""


class SyncroAPIError(RuntimeError):
    """Raised when Syncro responds with an error status."""


def _get_base_url() -> str:
    settings = get_settings()
    base = settings.syncro_webhook_url
    if not base:
        raise SyncroConfigurationError("SYNCRO_WEBHOOK_URL is not configured")
    url = str(base).rstrip("/")
    if not url.endswith("/api/v1"):
        url = f"{url}/api/v1"
    return url


async def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: Any | None = None,
    timeout: float = 15.0,
) -> Any:
    base_url = _get_base_url()
    url = f"{base_url}{path if path.startswith('/') else f'/{path}'}"
    headers: dict[str, str] = {}
    settings = get_settings()
    if settings.syncro_api_key:
        headers["Authorization"] = f"Bearer {settings.syncro_api_key}"
    log_info("Calling Syncro API", url=url, method=method)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
            )
        except httpx.HTTPError as exc:
            log_error("Syncro API request failed", url=url, error=str(exc))
            raise SyncroAPIError(str(exc)) from exc
    if response.status_code == httpx.codes.NOT_FOUND:
        return None
    if response.status_code >= 400:
        log_error(
            "Syncro API responded with error",
            url=url,
            status=response.status_code,
            body=response.text,
        )
        raise SyncroAPIError(f"Syncro API responded with {response.status_code}")
    if response.status_code == httpx.codes.NO_CONTENT:
        return None
    try:
        data = response.json()
    except ValueError:
        data = response.text
    return data


def _extract_collection(data: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [dict(item) if isinstance(item, dict) else item for item in data]
    for key in keys:
        nested = data.get(key) if isinstance(data, dict) else None
        if isinstance(nested, list):
            return [dict(item) if isinstance(item, dict) else item for item in nested]
    return []


async def get_contacts(customer_id: str | int) -> list[dict[str, Any]]:
    payload = await _request("GET", "/contacts", params={"customer_id": customer_id})
    return _extract_collection(payload, "contacts", "data")


async def get_customer(customer_id: str | int) -> dict[str, Any] | None:
    payload = await _request("GET", f"/customers/{customer_id}")
    if not payload:
        return None
    if isinstance(payload, dict) and "customer" in payload:
        customer = payload.get("customer")
        if isinstance(customer, dict):
            return customer
    return payload if isinstance(payload, dict) else None
