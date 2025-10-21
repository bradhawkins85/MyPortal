from __future__ import annotations

import asyncio
from collections import deque
from time import monotonic
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.repositories import integration_modules as module_repo
from app.services import webhook_monitor


class SyncroConfigurationError(RuntimeError):
    """Raised when Syncro integration settings are incomplete."""


class SyncroAPIError(RuntimeError):
    """Raised when Syncro responds with an error status."""


_MODULE_SETTINGS_CACHE: dict[str, Any] | None = None
_MODULE_SETTINGS_EXPIRY: float = 0.0
_MODULE_SETTINGS_LOCK = asyncio.Lock()
_RATE_LIMITER_CACHE: tuple[int, "AsyncRateLimiter"] | None = None
_RATE_LIMITER_LOCK = asyncio.Lock()


def _normalise_base_url(base: str) -> str:
    url = str(base or "").strip().rstrip("/")
    if not url:
        return ""
    if not url.endswith("/api/v1"):
        url = f"{url}/api/v1"
    return url


def _coerce_rate_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 180
    return limit if limit > 0 else 180


async def _load_module_settings() -> dict[str, Any] | None:
    global _MODULE_SETTINGS_CACHE, _MODULE_SETTINGS_EXPIRY
    now = monotonic()
    if _MODULE_SETTINGS_CACHE is not None and now < _MODULE_SETTINGS_EXPIRY:
        return _MODULE_SETTINGS_CACHE
    async with _MODULE_SETTINGS_LOCK:
        now = monotonic()
        if _MODULE_SETTINGS_CACHE is not None and now < _MODULE_SETTINGS_EXPIRY:
            return _MODULE_SETTINGS_CACHE
        try:
            module = await module_repo.get_module("syncro")
        except RuntimeError as exc:  # pragma: no cover - database may be unavailable during tests
            log_error("Unable to load Syncro module configuration", error=str(exc))
            module = None
        if not module:
            _MODULE_SETTINGS_CACHE = None
        else:
            settings_payload = module.get("settings") or {}
            _MODULE_SETTINGS_CACHE = {
                "enabled": bool(module.get("enabled")),
                "base_url": str(settings_payload.get("base_url") or "").strip(),
                "api_key": str(settings_payload.get("api_key") or "").strip(),
                "rate_limit_per_minute": _coerce_rate_limit(settings_payload.get("rate_limit_per_minute")),
            }
        _MODULE_SETTINGS_EXPIRY = now + 30.0
    return _MODULE_SETTINGS_CACHE


async def _get_effective_settings() -> dict[str, Any]:
    module_settings = await _load_module_settings()
    if module_settings and not module_settings.get("enabled"):
        raise SyncroConfigurationError("Syncro module is disabled")
    settings = get_settings()
    base_url = _normalise_base_url(
        module_settings.get("base_url") if module_settings else settings.syncro_webhook_url or ""
    )
    if not base_url:
        raise SyncroConfigurationError("Syncro base URL is not configured")
    api_key = str(module_settings.get("api_key") if module_settings else "").strip()
    if not api_key:
        api_key = str(settings.syncro_api_key or "").strip()
    rate_limit = (
        module_settings.get("rate_limit_per_minute")
        if module_settings
        else 180
    )
    return {
        "base_url": base_url,
        "api_key": api_key or None,
        "rate_limit_per_minute": _coerce_rate_limit(rate_limit),
    }


async def _get_or_create_rate_limiter(limit: int) -> "AsyncRateLimiter":
    global _RATE_LIMITER_CACHE
    async with _RATE_LIMITER_LOCK:
        if _RATE_LIMITER_CACHE and _RATE_LIMITER_CACHE[0] == limit:
            return _RATE_LIMITER_CACHE[1]
        limiter = AsyncRateLimiter(limit=limit, interval=60.0)
        _RATE_LIMITER_CACHE = (limit, limiter)
        return limiter


async def get_rate_limiter() -> "AsyncRateLimiter":
    module_settings = await _load_module_settings()
    if module_settings and not module_settings.get("enabled"):
        raise SyncroConfigurationError("Syncro module is disabled")
    limit = _coerce_rate_limit((module_settings or {}).get("rate_limit_per_minute"))
    return await _get_or_create_rate_limiter(limit)


class AsyncRateLimiter:
    """Coroutine-friendly token bucket limiting requests per interval."""

    __slots__ = ("_limit", "_interval", "_lock", "_events")

    def __init__(self, limit: int, interval: float) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._limit = limit
        self._interval = interval
        self._lock = asyncio.Lock()
        self._events: deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = monotonic()
                while self._events and now - self._events[0] >= self._interval:
                    self._events.popleft()
                if len(self._events) < self._limit:
                    self._events.append(now)
                    return
                earliest = self._events[0]
                wait_time = self._interval - (now - earliest)
            await asyncio.sleep(max(wait_time, 0.05))


async def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: Any | None = None,
    timeout: float = 15.0,
    rate_limiter: AsyncRateLimiter | None = None,
) -> Any:
    settings = await _get_effective_settings()
    limiter = rate_limiter or await _get_or_create_rate_limiter(settings["rate_limit_per_minute"])
    await limiter.acquire()
    base_url = settings["base_url"]
    url = f"{base_url}{path if path.startswith('/') else f'/{path}'}"
    headers: dict[str, str] = {}
    if settings.get("api_key"):
        headers["Authorization"] = f"Bearer {settings['api_key']}"
    log_info("Calling Syncro API", url=url, method=method)

    webhook_event: dict[str, Any] | None = None
    webhook_payload: dict[str, Any] = {"method": method.upper()}
    if params:
        webhook_payload["params"] = params
    if json is not None:
        webhook_payload["body"] = json
    try:
        webhook_event = await webhook_monitor.create_manual_event(
            name="syncro.api.request",
            target_url=url,
            payload=webhook_payload,
            headers=None,
            max_attempts=1,
            backoff_seconds=0,
        )
    except Exception as exc:  # pragma: no cover - webhook monitor safety
        log_error("Failed to record Syncro request in webhook monitor", url=url, error=str(exc))
        webhook_event = None

    event_id: int | None = None
    if webhook_event and webhook_event.get("id") is not None:
        try:
            event_id = int(webhook_event["id"])
        except (TypeError, ValueError):  # pragma: no cover - defensive
            event_id = None

    def _truncate_body(body: str | None) -> str | None:
        if body is None:
            return None
        if len(body) <= 4000:
            return body
        return body[:3997] + "..."

    def _normalise_params(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if hasattr(value, "items"):
            return dict(value.items())
        if isinstance(value, (list, tuple)):
            return list(value)
        return value

    request_snapshot = {
        "method": method,
        "params": _normalise_params(params),
        "json": json,
    }

    response_headers: Any = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
            )
            response_headers = getattr(response, "headers", None)
        except httpx.HTTPError as exc:
            log_error("Syncro API request failed", url=url, error=str(exc))
            if event_id is not None:
                try:
                    await webhook_monitor.record_manual_failure(
                        event_id,
                        attempt_number=1,
                        status="error",
                        error_message=str(exc),
                        response_status=None,
                        response_body=None,
                        request_headers=headers,
                        request_body=request_snapshot,
                        response_headers=None,
                    )
                except Exception as record_exc:  # pragma: no cover - logging safety
                    log_error(
                        "Failed to record Syncro webhook failure",
                        event_id=event_id,
                        error=str(record_exc),
                    )
            raise SyncroAPIError(str(exc)) from exc
    if response.status_code == httpx.codes.NOT_FOUND:
        if event_id is not None:
            try:
                await webhook_monitor.record_manual_success(
                    event_id,
                    attempt_number=1,
                    response_status=response.status_code,
                    response_body=_truncate_body(response.text),
                    request_headers=headers,
                    request_body=request_snapshot,
                    response_headers=response_headers,
                )
            except Exception as record_exc:  # pragma: no cover - logging safety
                log_error(
                    "Failed to record Syncro webhook success",
                    event_id=event_id,
                    error=str(record_exc),
                )
        return None
    if response.status_code >= 400:
        log_error(
            "Syncro API responded with error",
            url=url,
            status=response.status_code,
            body=response.text,
        )
        if event_id is not None:
            try:
                await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=1,
                    status="failed",
                    error_message=f"HTTP {response.status_code}",
                    response_status=response.status_code,
                    response_body=_truncate_body(response.text),
                    request_headers=headers,
                    request_body=request_snapshot,
                    response_headers=response_headers,
                )
            except Exception as record_exc:  # pragma: no cover - logging safety
                log_error(
                    "Failed to record Syncro webhook failure",
                    event_id=event_id,
                    error=str(record_exc),
                )
        raise SyncroAPIError(f"Syncro API responded with {response.status_code}")
    if response.status_code == httpx.codes.NO_CONTENT:
        if event_id is not None:
            try:
                await webhook_monitor.record_manual_success(
                    event_id,
                    attempt_number=1,
                    response_status=response.status_code,
                    response_body=None,
                    request_headers=headers,
                    request_body=request_snapshot,
                    response_headers=response_headers,
                )
            except Exception as record_exc:  # pragma: no cover - logging safety
                log_error(
                    "Failed to record Syncro webhook success",
                    event_id=event_id,
                    error=str(record_exc),
                )
        return None
    try:
        data = response.json()
    except ValueError:
        data = response.text
    if event_id is not None:
        try:
            await webhook_monitor.record_manual_success(
                event_id,
                attempt_number=1,
                response_status=response.status_code,
                response_body=_truncate_body(response.text),
                request_headers=headers,
                request_body=request_snapshot,
                response_headers=response_headers,
            )
        except Exception as record_exc:  # pragma: no cover - logging safety
            log_error(
                "Failed to record Syncro webhook success",
                event_id=event_id,
                error=str(record_exc),
            )
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


async def get_assets(customer_id: str | int) -> list[dict[str, Any]]:
    """Fetch all Syncro assets for a customer, handling pagination."""

    results: list[dict[str, Any]] = []
    for page in range(1, 101):
        payload = await _request(
            "GET",
            "/customer_assets",
            params={"customer_id": customer_id, "page": page},
        )
        if not payload:
            break
        assets = _extract_collection(payload, "assets", "data")
        if not assets:
            break
        results.extend(assets)

        total_pages: int | None = None
        if isinstance(payload, dict):
            meta = payload.get("meta")
            if isinstance(meta, dict) and meta.get("total_pages"):
                try:
                    total_pages = int(meta.get("total_pages"))
                except (TypeError, ValueError):
                    total_pages = None
            if total_pages is None:
                pagination = payload.get("pagination")
                if isinstance(pagination, dict) and pagination.get("total_pages"):
                    try:
                        total_pages = int(pagination.get("total_pages"))
                    except (TypeError, ValueError):
                        total_pages = None
        if total_pages and page >= total_pages:
            break
    return results


async def list_tickets(
    *,
    page: int = 1,
    per_page: int = 25,
    rate_limiter: AsyncRateLimiter | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch a page of Syncro tickets with pagination metadata."""

    params = {"page": page, "per_page": per_page}
    payload = await _request(
        "GET",
        "/tickets",
        params=params,
        rate_limiter=rate_limiter,
    )
    tickets = _extract_collection(payload, "tickets", "data")
    meta: dict[str, Any] = {}
    if isinstance(payload, dict):
        candidate = payload.get("meta")
        if isinstance(candidate, dict):
            meta = dict(candidate)
        else:
            candidate = payload.get("pagination")
            if isinstance(candidate, dict):
                meta = dict(candidate)
    return tickets, meta


async def get_ticket(
    ticket_id: str | int,
    *,
    rate_limiter: AsyncRateLimiter | None = None,
) -> dict[str, Any] | None:
    """Return a single Syncro ticket payload or ``None`` if not found."""

    payload = await _request(
        "GET",
        f"/tickets/{ticket_id}",
        rate_limiter=rate_limiter,
    )
    if not payload:
        return None
    if isinstance(payload, dict):
        ticket = payload.get("ticket") if "ticket" in payload else payload
        if isinstance(ticket, dict):
            return dict(ticket)
    return None


def _parse_numeric_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value):  # NaN check
            return None
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        digits = ""
        for ch in text:
            if ch.isdigit() or ch in {"-", "."}:
                digits += ch
            elif digits:
                break
        if not digits:
            return None
        try:
            return float(digits)
        except ValueError:
            return None


def extract_asset_details(asset: dict[str, Any]) -> dict[str, Any]:
    """Normalise Syncro asset payloads into a consistent schema."""

    props = asset.get("properties") if isinstance(asset, dict) else {}
    if not isinstance(props, dict):
        props = {}
    kabuto = props.get("kabuto_information") if isinstance(props, dict) else {}
    if not isinstance(kabuto, dict):
        kabuto = {}
    general = kabuto.get("general") if isinstance(kabuto, dict) else {}
    if not isinstance(general, dict):
        general = {}

    cpu_array = kabuto.get("cpu") if isinstance(kabuto, dict) else []
    if not isinstance(cpu_array, list):
        cpu_array = []
    hdd_array = kabuto.get("hdd") if isinstance(kabuto, dict) else []
    if not isinstance(hdd_array, list):
        hdd_array = []
    ram_array = kabuto.get("ram") if isinstance(kabuto, dict) else []
    if not isinstance(ram_array, list):
        ram_array = []

    performance_candidates = [
        asset.get("performance_score"),
        props.get("performance_score"),
        kabuto.get("performance_score"),
        props.get("Performance Score"),
    ]
    performance: float | None = None
    for candidate in performance_candidates:
        parsed = _parse_numeric_value(candidate)
        if parsed is not None:
            performance = parsed
            break

    ram_value = (
        _parse_numeric_value(asset.get("ram_gb"))
        or _parse_numeric_value(props.get("ram_gb"))
        or _parse_numeric_value(kabuto.get("ram_gb"))
    )
    if ram_value is None and ram_array:
        first_ram = ram_array[0]
        if isinstance(first_ram, dict):
            ram_value = _parse_numeric_value(first_ram.get("size"))
        else:
            ram_value = _parse_numeric_value(first_ram)

    hdd_size = asset.get("hdd_size") or props.get("hdd_size")
    if not hdd_size and hdd_array:
        first_hdd = hdd_array[0]
        if isinstance(first_hdd, dict):
            hdd_size = first_hdd.get("size")
        else:
            hdd_size = first_hdd
    if not hdd_size and isinstance(props.get("hdd"), str):
        hdd_size = props.get("hdd")

    os_name = (
        asset.get("os_name")
        or props.get("os_name")
        or props.get("os")
        or (kabuto.get("os", {}).get("name") if isinstance(kabuto.get("os"), dict) else None)
    )

    cpu_name = asset.get("cpu_name") or props.get("cpu_name")
    if not cpu_name and cpu_array:
        first_cpu = cpu_array[0]
        if isinstance(first_cpu, dict):
            cpu_name = first_cpu.get("name")
        else:
            cpu_name = first_cpu

    last_sync = asset.get("last_sync") or props.get("last_sync") or kabuto.get("last_synced_at")

    motherboard_manufacturer = (
        asset.get("motherboard_manufacturer")
        or props.get("motherboard_manufacturer")
        or (kabuto.get("motherboard", {}).get("manufacturer") if isinstance(kabuto.get("motherboard"), dict) else None)
    )

    form_factor = (
        asset.get("form_factor")
        or props.get("form_factor")
        or kabuto.get("form_factor")
        or general.get("form_factor")
    )

    last_user = asset.get("last_user") or props.get("last_user") or kabuto.get("last_user")

    cpu_age_candidates = [
        asset.get("cpu_age"),
        props.get("cpu_age"),
        kabuto.get("cpu_age"),
        general.get("cpu_age"),
        asset.get("CPUAge"),
        props.get("CPUAge"),
        kabuto.get("CPUAge"),
        general.get("CPUAge"),
        asset.get("CPU Age"),
        props.get("CPU Age"),
        kabuto.get("CPU Age"),
        general.get("CPU Age"),
        asset.get("approx_age"),
        props.get("approx_age"),
        kabuto.get("approx_age"),
        general.get("approx_age"),
    ]
    approx_age: float | None = None
    for candidate in cpu_age_candidates:
        parsed = _parse_numeric_value(candidate)
        if parsed is not None:
            approx_age = parsed
            break

    warranty_status = asset.get("warranty_status") or props.get("warranty_status")
    warranty_end = asset.get("warranty_end_date") or props.get("warranty_end_date")

    return {
        "id": asset.get("id"),
        "name": asset.get("name")
        or props.get("device_name")
        or general.get("name"),
        "type": asset.get("type") or props.get("type") or general.get("type"),
        "serial_number": asset.get("serial_number")
        or props.get("serial_number")
        or general.get("serial_number"),
        "status": asset.get("status") or props.get("status"),
        "os_name": os_name,
        "cpu_name": cpu_name,
        "ram_gb": ram_value,
        "hdd_size": hdd_size,
        "last_sync": last_sync,
        "motherboard_manufacturer": motherboard_manufacturer,
        "form_factor": form_factor,
        "last_user": last_user,
        "cpu_age": approx_age,
        "performance_score": performance,
        "warranty_status": warranty_status,
        "warranty_end_date": warranty_end,
    }
