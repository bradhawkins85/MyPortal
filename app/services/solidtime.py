"""Solidtime integration service.

This module provides the HTTP client, sync helpers, and conversion utilities
used to synchronise MyPortal tickets and ticket time entries with Solidtime
(https://github.com/solidtime-io/solidtime).

Concept mapping
---------------
* MyPortal company → Solidtime client
* MyPortal ticket → Solidtime project (one project per ticket; the project
  name is ``#<ticket_number> – <subject>``).
* MyPortal ticket reply with ``minutes_spent`` → Solidtime time entry.
  The end timestamp is the reply's ``created_at`` interpreted as UTC; the
  start is computed by subtracting ``minutes_spent`` from the end.
* MyPortal labour type → optional Solidtime task within the project.
* MyPortal user → Solidtime member, matched by email.

The HTTP layer mirrors the structure of :mod:`app.services.syncro`: a cached
module-settings loader, a coroutine-friendly token-bucket rate limiter, a
thin :func:`_request` helper that records every outbound call through the
project's :mod:`app.services.webhook_monitor`, and resource helpers returning
plain dicts.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Mapping
from urllib.parse import urlparse

import nh3
import httpx
from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import log_error, log_info, log_warning
from app.repositories import companies as company_repo
from app.repositories import solidtime_links as links_repo
from app.repositories import tickets as tickets_repo
from app.repositories import users as user_repo
from app.services import modules as modules_service
from app.services import rate_limit_store
from app.services import webhook_monitor
from app.services.redis import get_redis_client

# Backwards-compatible alias used by the Solidtime unit tests and older code.
module_repo = modules_service

SOLIDTIME_MODULE_SLUG = "solidtime"
DEFAULT_RATE_LIMIT_PER_MINUTE = 120
REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


class SolidtimeConfigurationError(RuntimeError):
    """Raised when Solidtime integration settings are incomplete or disabled."""


class SolidtimeAPIError(RuntimeError):
    """Raised when the Solidtime API returns an error status."""


# ---------------------------------------------------------------------------
# Settings & module helpers
# ---------------------------------------------------------------------------

_MODULE_SETTINGS_CACHE: dict[str, Any] | None = None
_MODULE_SETTINGS_EXPIRY: float = 0.0
_MODULE_SETTINGS_LOCK = asyncio.Lock()
_RATE_LIMITER_CACHE: tuple[int, "AsyncRateLimiter"] | None = None
_RATE_LIMITER_LOCK = asyncio.Lock()


def _normalise_base_url(base: str) -> str:
    """Return a normalised Solidtime base URL.

    The Solidtime REST API lives under ``/api/v1`` on the configured host.
    Only ``http``/``https`` schemes are accepted to prevent malformed input
    from being passed straight to ``httpx``.
    """
    url = str(base or "").strip().rstrip("/")
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SolidtimeConfigurationError(
            "Solidtime base URL must include an http(s) scheme and host"
        )
    if not url.endswith("/api/v1"):
        url = f"{url}/api/v1"
    return url


def _coerce_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 600) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if result < minimum:
        return default
    if result > maximum:
        return maximum
    return result


def _reset_settings_cache() -> None:
    """Invalidate the cached module settings (used by tests and admin save)."""
    global _MODULE_SETTINGS_CACHE, _MODULE_SETTINGS_EXPIRY
    _MODULE_SETTINGS_CACHE = None
    _MODULE_SETTINGS_EXPIRY = 0.0


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
            try:
                module = await modules_service.get_module(
                    SOLIDTIME_MODULE_SLUG, redact=False
                )
            except TypeError:
                # Some tests and older service implementations expose a
                # single-argument get_module helper. Fall back without the
                # redact flag while keeping production calls unredacted.
                module = await modules_service.get_module(SOLIDTIME_MODULE_SLUG)
        except RuntimeError as exc:  # pragma: no cover - defensive
            log_error("Unable to load Solidtime module configuration", error=str(exc))
            module = None
        if not module:
            _MODULE_SETTINGS_CACHE = None
        else:
            settings_payload = module.get("settings") or {}
            _MODULE_SETTINGS_CACHE = {
                "enabled": bool(module.get("enabled")),
                "base_url": str(settings_payload.get("base_url") or "").strip(),
                "api_token": str(settings_payload.get("api_token") or "").strip(),
                "organization_id": str(
                    settings_payload.get("organization_id") or ""
                ).strip(),
                "default_client_id": str(
                    settings_payload.get("default_client_id") or ""
                ).strip(),
                "sync_tickets_to_projects": bool(
                    settings_payload.get("sync_tickets_to_projects", True)
                ),
                "sync_projects_to_tickets": bool(
                    settings_payload.get("sync_projects_to_tickets", False)
                ),
                "sync_time_entries_to_solidtime": bool(
                    settings_payload.get("sync_time_entries_to_solidtime", True)
                ),
                "sync_time_entries_from_solidtime": bool(
                    settings_payload.get("sync_time_entries_from_solidtime", True)
                ),
                "only_billable_to_solidtime": bool(
                    settings_payload.get("only_billable_to_solidtime", False)
                ),
                "labour_type_to_task": bool(
                    settings_payload.get("labour_type_to_task", False)
                ),
                "webhook_secret": str(
                    settings_payload.get("webhook_secret") or ""
                ).strip(),
                "rate_limit_per_minute": _coerce_int(
                    settings_payload.get("rate_limit_per_minute"),
                    DEFAULT_RATE_LIMIT_PER_MINUTE,
                ),
            }
        _MODULE_SETTINGS_EXPIRY = now + 30.0
    return _MODULE_SETTINGS_CACHE


async def is_module_enabled() -> bool:
    """Return True only when the module is enabled and configured."""
    settings = await _load_module_settings()
    if not settings or not settings.get("enabled"):
        return False
    return bool(settings.get("base_url") and settings.get("api_token"))


async def get_settings_snapshot() -> dict[str, Any]:
    """Return a redacted snapshot for admin status pages and tests."""
    settings = await _load_module_settings() or {}
    snapshot = dict(settings)
    if snapshot.get("api_token"):
        snapshot["api_token"] = "********"
    if snapshot.get("webhook_secret"):
        snapshot["webhook_secret"] = "********"
    return snapshot


async def _get_effective_settings() -> dict[str, Any]:
    settings = await _load_module_settings()
    if not settings:
        raise SolidtimeConfigurationError("Solidtime module is not configured")
    if not settings.get("enabled"):
        raise SolidtimeConfigurationError("Solidtime module is disabled")
    base_url = _normalise_base_url(settings.get("base_url") or "")
    if not base_url:
        raise SolidtimeConfigurationError("Solidtime base URL is not configured")
    api_token = (settings.get("api_token") or "").strip()
    if not api_token:
        raise SolidtimeConfigurationError("Solidtime API token is not configured")
    return {**settings, "base_url": base_url, "api_token": api_token}


# ---------------------------------------------------------------------------
# Rate limiter (mirrors syncro pattern)
# ---------------------------------------------------------------------------

class AsyncRateLimiter:
    """Token-bucket limiter sharing slots via Redis when available."""

    __slots__ = (
        "_limit",
        "_interval",
        "_lock",
        "_events",
        "_redis",
        "_namespace",
        "_redis_failed",
    )

    def __init__(
        self,
        limit: int,
        interval: float,
        *,
        redis_client: Redis | None = None,
        namespace: str = "solidtime-api",
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._limit = limit
        self._interval = interval
        self._lock = asyncio.Lock()
        self._events: deque[float] = deque()
        self._redis = redis_client
        self._namespace = namespace
        self._redis_failed = False

    async def _acquire_with_redis(self) -> bool:
        if self._redis is None:
            raise RuntimeError("Redis client not configured for rate limiter")
        redis_key = f"{self._namespace}:{self._limit}:{int(self._interval)}"
        try:
            allowed, retry_after = await rate_limit_store.acquire_slot(
                self._redis,
                key=redis_key,
                limit=self._limit,
                window_seconds=self._interval,
            )
        except RedisError as exc:
            if not self._redis_failed:
                log_warning("Redis Solidtime rate limiter unavailable", error=str(exc))
                self._redis_failed = True
            self._redis = None
            return False
        if allowed:
            return True
        await asyncio.sleep(max(retry_after or 0.05, 0.05))
        return False

    async def acquire(self) -> None:
        while True:
            if self._redis is not None:
                if await self._acquire_with_redis():
                    return
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


async def _get_or_create_rate_limiter(limit: int) -> AsyncRateLimiter:
    global _RATE_LIMITER_CACHE
    async with _RATE_LIMITER_LOCK:
        if _RATE_LIMITER_CACHE and _RATE_LIMITER_CACHE[0] == limit:
            return _RATE_LIMITER_CACHE[1]
        limiter = AsyncRateLimiter(
            limit=limit,
            interval=60.0,
            redis_client=get_redis_client(),
            namespace="solidtime-api",
        )
        _RATE_LIMITER_CACHE = (limit, limiter)
        return limiter


# ---------------------------------------------------------------------------
# HTTP request helper
# ---------------------------------------------------------------------------

def _truncate_body(body: str | None) -> str | None:
    if body is None:
        return None
    if len(body) <= 4000:
        return body
    return body[:3997] + "..."


async def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any | None = None,
    timeout: httpx.Timeout | float = REQUEST_TIMEOUT,
    rate_limiter: AsyncRateLimiter | None = None,
) -> Any:
    settings = await _get_effective_settings()
    limiter = rate_limiter or await _get_or_create_rate_limiter(
        int(settings.get("rate_limit_per_minute") or DEFAULT_RATE_LIMIT_PER_MINUTE)
    )
    await limiter.acquire()
    base_url = settings["base_url"]
    url = f"{base_url}{path if path.startswith('/') else f'/{path}'}"
    headers: dict[str, str] = {
        "Authorization": f"Bearer {settings['api_token']}",
        "Accept": "application/json",
    }

    log_info("Calling Solidtime API", url=url, method=method)

    webhook_event: dict[str, Any] | None = None
    webhook_payload: dict[str, Any] = {"method": method.upper()}
    if params:
        webhook_payload["params"] = params
    if json_body is not None:
        webhook_payload["body"] = json_body
    try:
        webhook_event = await webhook_monitor.create_manual_event(
            name="solidtime.api.request",
            target_url=url,
            payload=webhook_payload,
            headers=None,
            max_attempts=1,
            backoff_seconds=0,
        )
    except Exception as exc:  # pragma: no cover - webhook monitor safety
        log_error(
            "Failed to record Solidtime request in webhook monitor",
            url=url,
            error=str(exc),
        )
        webhook_event = None

    event_id: int | None = None
    if webhook_event and webhook_event.get("id") is not None:
        try:
            event_id = int(webhook_event["id"])
        except (TypeError, ValueError):  # pragma: no cover - defensive
            event_id = None

    request_snapshot = {
        "method": method,
        "params": dict(params) if isinstance(params, dict) else params,
        "json": json_body,
    }
    response_headers: Any = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
            )
            response_headers = getattr(response, "headers", None)
        except httpx.HTTPError as exc:
            log_error("Solidtime API request failed", url=url, error=str(exc))
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
                except Exception as record_exc:  # pragma: no cover
                    log_error(
                        "Failed to record Solidtime webhook failure",
                        event_id=event_id,
                        error=str(record_exc),
                    )
            raise SolidtimeAPIError(str(exc)) from exc

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
            except Exception as record_exc:  # pragma: no cover
                log_error(
                    "Failed to record Solidtime webhook success",
                    event_id=event_id,
                    error=str(record_exc),
                )
        return None
    if response.status_code >= 400:
        log_error(
            "Solidtime API responded with error",
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
            except Exception as record_exc:  # pragma: no cover
                log_error(
                    "Failed to record Solidtime webhook failure",
                    event_id=event_id,
                    error=str(record_exc),
                )
        response_body = _truncate_body(response.text)
        detail = f"Solidtime API responded with {response.status_code}"
        if response_body:
            detail = f"{detail}: {response_body}"
        raise SolidtimeAPIError(detail)
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
            except Exception as record_exc:  # pragma: no cover
                log_error(
                    "Failed to record Solidtime webhook success",
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
        except Exception as record_exc:  # pragma: no cover
            log_error(
                "Failed to record Solidtime webhook success",
                event_id=event_id,
                error=str(record_exc),
            )
    return data


def _extract_data(payload: Any) -> Any:
    """Solidtime wraps responses in ``{ "data": ... }`` for resource endpoints."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


# ---------------------------------------------------------------------------
# Resource helpers
# ---------------------------------------------------------------------------

async def list_organizations() -> list[dict[str, Any]]:
    payload = await _request("GET", "/users/me/memberships")
    data = _extract_data(payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


async def list_clients(org_id: str) -> list[dict[str, Any]]:
    payload = await _request("GET", f"/organizations/{org_id}/clients")
    data = _extract_data(payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


async def create_client(org_id: str, *, name: str) -> dict[str, Any]:
    payload = await _request(
        "POST",
        f"/organizations/{org_id}/clients",
        json_body={"name": name},
    )
    data = _extract_data(payload)
    return data if isinstance(data, dict) else {}


async def update_client(org_id: str, client_id: str, *, name: str) -> dict[str, Any]:
    payload = await _request(
        "PUT",
        f"/organizations/{org_id}/clients/{client_id}",
        json_body={"name": name},
    )
    data = _extract_data(payload)
    return data if isinstance(data, dict) else {}


async def list_projects(org_id: str) -> list[dict[str, Any]]:
    payload = await _request("GET", f"/organizations/{org_id}/projects")
    data = _extract_data(payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


async def get_project(org_id: str, project_id: str) -> dict[str, Any] | None:
    payload = await _request("GET", f"/organizations/{org_id}/projects/{project_id}")
    if payload is None:
        return None
    data = _extract_data(payload)
    return data if isinstance(data, dict) else None


async def create_project(org_id: str, *, body: dict[str, Any]) -> dict[str, Any]:
    payload = await _request(
        "POST", f"/organizations/{org_id}/projects", json_body=body
    )
    data = _extract_data(payload)
    return data if isinstance(data, dict) else {}


async def update_project(
    org_id: str, project_id: str, *, body: dict[str, Any]
) -> dict[str, Any]:
    payload = await _request(
        "PUT",
        f"/organizations/{org_id}/projects/{project_id}",
        json_body=body,
    )
    data = _extract_data(payload)
    return data if isinstance(data, dict) else {}


async def archive_project(org_id: str, project_id: str) -> dict[str, Any]:
    return await update_project(org_id, project_id, body={"is_archived": True})


async def list_tasks(org_id: str, project_id: str) -> list[dict[str, Any]]:
    payload = await _request(
        "GET",
        f"/organizations/{org_id}/projects/{project_id}/tasks",
    )
    data = _extract_data(payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


async def create_task(
    org_id: str, project_id: str, *, name: str
) -> dict[str, Any]:
    payload = await _request(
        "POST",
        f"/organizations/{org_id}/projects/{project_id}/tasks",
        json_body={"name": name},
    )
    data = _extract_data(payload)
    return data if isinstance(data, dict) else {}


async def list_members(org_id: str) -> list[dict[str, Any]]:
    payload = await _request("GET", f"/organizations/{org_id}/members")
    data = _extract_data(payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


async def find_member_by_email(org_id: str, email: str) -> dict[str, Any] | None:
    if not email:
        return None
    needle = email.strip().lower()
    if not needle:
        return None
    members = await list_members(org_id)
    for member in members:
        candidate = str(member.get("email") or "").strip().lower()
        if candidate and candidate == needle:
            return member
    return None


async def list_time_entries(
    org_id: str,
    *,
    project_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if project_id:
        params["project_id"] = project_id
    if start:
        params["start"] = start.astimezone(timezone.utc).isoformat()
    if end:
        params["end"] = end.astimezone(timezone.utc).isoformat()
    payload = await _request(
        "GET",
        f"/organizations/{org_id}/time-entries",
        params=params or None,
    )
    data = _extract_data(payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


async def create_time_entry(
    org_id: str, *, body: dict[str, Any]
) -> dict[str, Any]:
    payload = await _request(
        "POST",
        f"/organizations/{org_id}/time-entries",
        json_body=body,
    )
    data = _extract_data(payload)
    return data if isinstance(data, dict) else {}


async def update_time_entry(
    org_id: str, time_entry_id: str, *, body: dict[str, Any]
) -> dict[str, Any]:
    payload = await _request(
        "PUT",
        f"/organizations/{org_id}/time-entries/{time_entry_id}",
        json_body=body,
    )
    data = _extract_data(payload)
    return data if isinstance(data, dict) else {}


async def delete_time_entry(org_id: str, time_entry_id: str) -> None:
    await _request("DELETE", f"/organizations/{org_id}/time-entries/{time_entry_id}")


# ---------------------------------------------------------------------------
# Conversion utilities
# ---------------------------------------------------------------------------

def _ticket_project_name(ticket: Mapping[str, Any]) -> str:
    number = (
        ticket.get("ticket_number")
        or ticket.get("external_reference")
        or ticket.get("id")
    )
    subject = str(ticket.get("subject") or "Ticket").strip() or "Ticket"
    if number is None:
        return subject
    return f"#{number} – {subject}"


def _project_archived_for_status(status_value: Any) -> bool:
    text = str(status_value or "").strip().lower()
    return text in {"closed", "resolved"}


def _ticket_project_color(ticket: Mapping[str, Any]) -> str:
    """Return a stable Solidtime-compatible hex color for a ticket project.

    Solidtime requires a ``color`` value when projects are created or updated.
    Deriving it from the ticket identity keeps retries idempotent and avoids
    changing a project's colour on later sync attempts.
    """
    seed = str(
        ticket.get("id")
        or ticket.get("ticket_number")
        or ticket.get("external_reference")
        or _ticket_project_name(ticket)
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"#{digest[:6]}"


def ticket_to_project_payload(
    ticket: Mapping[str, Any],
    *,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Build a Solidtime project payload from a MyPortal ticket record."""
    body: dict[str, Any] = {
        "name": _ticket_project_name(ticket),
        "color": _ticket_project_color(ticket),
        "is_billable": True,
        "is_archived": _project_archived_for_status(ticket.get("status")),
        # Solidtime validates ``client_id`` as present+nullable on project
        # create/update requests, so include the key even when unassigned.
        "client_id": client_id or None,
    }
    description = ticket.get("description")
    if isinstance(description, str) and description.strip():
        # Keep the description short; ticket descriptions can be very large.
        body["description"] = description.strip()[:1000]
    return body


def _first_line_of_body(body: Any, limit: int = 240) -> str:
    """Return a short, safe single-line summary of a ticket reply body.

    Reply bodies are user-submitted HTML. To produce a description suitable
    for a Solidtime time entry, the function:

    * uses :mod:`bleach` to remove all HTML tags (per the project sanitisation
      convention) so no markup leaks into the upstream system;
    * normalises non-breaking spaces to regular spaces;
    * keeps only the first non-empty line; and
    * truncates the result to ``limit`` characters with an ellipsis.
    """
    if not body:
        return ""
    text = nh3.clean(str(body), tags=frozenset())
    text = text.replace("\xa0", " ").strip()
    if not text:
        return ""
    line = text.splitlines()[0].strip()
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def _coerce_aware_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _extract_resource_id(value: Any) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get("id")
    else:
        candidate = value
    text = str(candidate or "").strip()
    return text or None


def _time_entry_description(entry: Mapping[str, Any]) -> str:
    return (
        _first_line_of_body(entry.get("description"))
        or _first_line_of_body(entry.get("note"))
        or "Imported from Solidtime"
    )


def _time_entry_billable(entry: Mapping[str, Any]) -> bool:
    if "billable" in entry:
        return _coerce_bool(entry.get("billable"))
    if "is_billable" in entry:
        return _coerce_bool(entry.get("is_billable"))
    return False


def _time_entry_project_id(entry: Mapping[str, Any]) -> str | None:
    return _extract_resource_id(entry.get("project_id")) or _extract_resource_id(
        entry.get("project")
    )


def _time_entry_member_id(entry: Mapping[str, Any]) -> str | None:
    return _extract_resource_id(entry.get("member_id")) or _extract_resource_id(
        entry.get("member")
    )


def _time_entry_task_id(entry: Mapping[str, Any]) -> str | None:
    return _extract_resource_id(entry.get("task_id")) or _extract_resource_id(
        entry.get("task")
    )


def _time_entry_minutes(entry: Mapping[str, Any]) -> int:
    start_dt = _coerce_aware_utc(entry.get("start"))
    end_dt = _coerce_aware_utc(entry.get("end"))
    if not start_dt or not end_dt:
        return 0
    seconds = (end_dt - start_dt).total_seconds()
    if seconds <= 0:
        return 0
    return int(seconds // 60)


def _time_entry_payload(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    project_id = _time_entry_project_id(entry)
    start_dt = _coerce_aware_utc(entry.get("start"))
    end_dt = _coerce_aware_utc(entry.get("end"))
    if not project_id or not start_dt or not end_dt or end_dt <= start_dt:
        return None
    body: dict[str, Any] = {
        "project_id": project_id,
        "start": start_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "end": end_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "billable": _time_entry_billable(entry),
        "description": _time_entry_description(entry),
    }
    member_id = _time_entry_member_id(entry)
    task_id = _time_entry_task_id(entry)
    if member_id:
        body["member_id"] = member_id
    if task_id:
        body["task_id"] = task_id
    return body


def reply_to_time_entry_payload(
    reply: Mapping[str, Any],
    ticket: Mapping[str, Any],
    *,
    project_id: str,
    member_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any] | None:
    """Convert a ticket reply into a Solidtime time entry payload.

    The end time is the reply's ``created_at`` interpreted as UTC; the start
    time is computed by subtracting ``minutes_spent`` from the end. Returns
    ``None`` when the reply has no positive ``minutes_spent`` value.
    """
    minutes = reply.get("minutes_spent")
    try:
        minutes_int = int(minutes) if minutes is not None else 0
    except (TypeError, ValueError):
        minutes_int = 0
    if minutes_int <= 0:
        return None

    end_dt = _coerce_aware_utc(reply.get("created_at")) or datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(minutes=minutes_int)
    body: dict[str, Any] = {
        "project_id": project_id,
        "start": start_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "end": end_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "billable": bool(reply.get("is_billable")),
        "description": _first_line_of_body(reply.get("body"))
        or f"Ticket #{ticket.get('id')}",
    }
    if member_id:
        body["member_id"] = member_id
    if task_id:
        body["task_id"] = task_id
    return body


def _hash_payload(payload: Any) -> str:
    sensitive_keys = {
        "api_token",
        "token",
        "password",
        "secret",
        "authorization",
    }

    def _redact_sensitive(value: Any) -> Any:
        if isinstance(value, Mapping):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text.lower() in sensitive_keys:
                    redacted[key_text] = "***REDACTED***"
                else:
                    redacted[key_text] = _redact_sensitive(item)
            return redacted
        if isinstance(value, list):
            return [_redact_sensitive(item) for item in value]
        if isinstance(value, tuple):
            return tuple(_redact_sensitive(item) for item in value)
        return value

    safe_payload = _redact_sensitive(payload)
    try:
        serialised = json.dumps(safe_payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        serialised = repr(safe_payload)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def _is_duplicate_project_error(exc: SolidtimeAPIError) -> bool:
    """Return True when Solidtime rejected a project create as a duplicate."""
    message = str(exc).lower()
    return "project with the same name and client already exists" in message or (
        "same name" in message and "client" in message and "already exists" in message
    )


def _project_client_id(project: Mapping[str, Any]) -> str | None:
    """Extract a Solidtime project client ID from flat or expanded API shapes."""
    return _extract_resource_id(project.get("client_id")) or _extract_resource_id(
        project.get("client")
    )


async def _find_existing_project_by_name_and_client(
    org_id: str,
    *,
    name: str,
    client_id: str | None,
) -> dict[str, Any] | None:
    """Find an existing Solidtime project matching the unique name/client pair.

    Solidtime enforces project uniqueness by organization, project name, and
    client. If MyPortal loses or has not yet created the local link row, a
    create attempt returns HTTP 422. Looking up the existing project lets the
    sync become idempotent and rebuild the missing local link instead of
    retrying the same failing create on every reconcile run.
    """
    projects = await list_projects(org_id)
    expected_name = name.strip()
    expected_client = str(client_id or "").strip() or None
    for project in projects:
        project_name = str(project.get("name") or "").strip()
        if project_name != expected_name:
            continue
        project_client = _project_client_id(project)
        if (project_client or None) == expected_client:
            return project
    return None


# ---------------------------------------------------------------------------
# Outbound sync helpers
# ---------------------------------------------------------------------------

async def _ensure_client_for_company(
    company: Mapping[str, Any], *, settings: Mapping[str, Any]
) -> str | None:
    """Return the Solidtime client ID for a MyPortal company.

    Creates and links one if missing.  Falls back to ``default_client_id``
    when the company name is empty.
    """
    company_id = company.get("id")
    if not isinstance(company_id, int):
        return settings.get("default_client_id") or None
    org_id = settings["organization_id"]
    existing = await links_repo.get_client_link(company_id)
    if existing and existing.get("solidtime_client_id"):
        return str(existing["solidtime_client_id"])
    name = str(company.get("name") or "").strip()
    if not name:
        return settings.get("default_client_id") or None
    try:
        created = await create_client(org_id, name=name)
    except SolidtimeAPIError as exc:
        await links_repo.mark_client_link_error(company_id, str(exc))
        raise
    client_id = str(created.get("id") or "").strip()
    if not client_id:
        await links_repo.mark_client_link_error(
            company_id, "Solidtime did not return a client ID"
        )
        return settings.get("default_client_id") or None
    await links_repo.upsert_client_link(
        company_id=company_id,
        solidtime_org_id=org_id,
        solidtime_client_id=client_id,
        sync_status="synced",
    )
    return client_id


async def sync_ticket_to_project(ticket_id: int) -> dict[str, Any] | None:
    """Push a ticket to Solidtime as a project.

    Returns the link record on success, ``None`` if the module is disabled.
    Errors are stored against the link row and re-raised so the scheduled
    reconciler can retry.
    """
    try:
        settings = await _get_effective_settings()
    except SolidtimeConfigurationError as exc:
        log_info(
            "Solidtime ticket sync skipped",
            ticket_id=ticket_id,
            reason=str(exc),
        )
        return None
    if not settings.get("sync_tickets_to_projects"):
        log_info(
            "Solidtime ticket sync skipped",
            ticket_id=ticket_id,
            reason="sync_tickets_to_projects is disabled",
        )
        return None

    ticket = await tickets_repo.get_ticket(int(ticket_id))
    if not ticket:
        log_info(
            "Solidtime ticket sync skipped",
            ticket_id=ticket_id,
            reason="ticket was not found",
        )
        return None

    org_id = settings["organization_id"]
    if not org_id:
        await links_repo.mark_project_link_error(
            int(ticket_id), "Solidtime organization_id is not configured"
        )
        raise SolidtimeConfigurationError(
            "Solidtime organization_id is not configured"
        )

    company_id = ticket.get("company_id")
    client_id: str | None = None
    if isinstance(company_id, int):
        company = await company_repo.get_company_by_id(company_id)
        if company:
            try:
                client_id = await _ensure_client_for_company(
                    company, settings=settings
                )
            except SolidtimeAPIError as exc:
                await links_repo.mark_project_link_error(int(ticket_id), str(exc))
                raise
    client_id = client_id or settings.get("default_client_id") or None

    body = ticket_to_project_payload(ticket, client_id=client_id)
    payload_hash = _hash_payload(body)

    existing = await links_repo.get_project_link(int(ticket_id))
    try:
        if existing and existing.get("solidtime_project_id"):
            project_id = str(existing["solidtime_project_id"])
            if existing.get("last_payload_hash") == payload_hash:
                # Nothing changed; just refresh the synced timestamp.
                return await links_repo.upsert_project_link(
                    ticket_id=int(ticket_id),
                    solidtime_org_id=org_id,
                    solidtime_project_id=project_id,
                    payload_hash=payload_hash,
                    sync_status="synced",
                )
            await update_project(org_id, project_id, body=body)
        else:
            try:
                created = await create_project(org_id, body=body)
            except SolidtimeAPIError as exc:
                if not _is_duplicate_project_error(exc):
                    raise
                existing_project = await _find_existing_project_by_name_and_client(
                    org_id,
                    name=str(body.get("name") or ""),
                    client_id=client_id,
                )
                if not existing_project:
                    raise
                created = existing_project
                log_info(
                    "Linked existing Solidtime project after duplicate create",
                    ticket_id=ticket_id,
                    project_id=str(existing_project.get("id") or ""),
                )
            project_id = str(created.get("id") or "").strip()
            if not project_id:
                raise SolidtimeAPIError("Solidtime did not return a project ID")
    except SolidtimeAPIError as exc:
        await links_repo.mark_project_link_error(int(ticket_id), str(exc))
        raise
    return await links_repo.upsert_project_link(
        ticket_id=int(ticket_id),
        solidtime_org_id=org_id,
        solidtime_project_id=project_id,
        payload_hash=payload_hash,
        sync_status="synced",
    )


async def _resolve_member_id_for_user(
    org_id: str, user_id: int | None
) -> str | None:
    if not isinstance(user_id, int) or user_id <= 0:
        return None
    link = await links_repo.get_user_link(user_id)
    if link and link.get("solidtime_member_id"):
        return str(link["solidtime_member_id"])
    user = await user_repo.get_user_by_id(user_id)
    if not user:
        return None
    email = str(user.get("email") or "").strip().lower()
    if not email:
        return None
    try:
        member = await find_member_by_email(org_id, email)
    except SolidtimeAPIError as exc:  # pragma: no cover - defensive
        log_warning("Unable to look up Solidtime member by email", error=str(exc))
        return None
    if not member:
        return None
    member_id = str(member.get("id") or "").strip()
    if not member_id:
        return None
    await links_repo.upsert_user_link(
        user_id=user_id,
        solidtime_org_id=org_id,
        solidtime_member_id=member_id,
    )
    return member_id


async def sync_reply_to_time_entry(reply_id: int) -> dict[str, Any] | None:
    """Push a ticket reply to Solidtime as a time entry."""
    try:
        settings = await _get_effective_settings()
    except SolidtimeConfigurationError:
        return None
    if not settings.get("sync_time_entries_to_solidtime"):
        return None

    reply = await tickets_repo.get_reply_by_id(int(reply_id))
    if not reply:
        return None
    minutes = reply.get("minutes_spent")
    try:
        minutes_int = int(minutes) if minutes is not None else 0
    except (TypeError, ValueError):
        minutes_int = 0
    if minutes_int <= 0:
        # No billable/non-billable time to log; clean up any prior entry.
        existing = await links_repo.get_time_entry_link(int(reply_id))
        if existing and existing.get("solidtime_time_entry_id"):
            try:
                await delete_time_entry(
                    str(existing["solidtime_org_id"]),
                    str(existing["solidtime_time_entry_id"]),
                )
            except SolidtimeAPIError as exc:  # pragma: no cover - defensive
                log_warning(
                    "Failed to delete stale Solidtime time entry",
                    reply_id=reply_id,
                    error=str(exc),
                )
                return None
            await links_repo.delete_time_entry_link(int(reply_id))
        return None

    if settings.get("only_billable_to_solidtime") and not bool(reply.get("is_billable")):
        existing = await links_repo.get_time_entry_link(int(reply_id))
        if existing and existing.get("solidtime_time_entry_id"):
            try:
                await delete_time_entry(
                    str(existing["solidtime_org_id"]),
                    str(existing["solidtime_time_entry_id"]),
                )
            except SolidtimeAPIError as exc:  # pragma: no cover - defensive
                log_warning(
                    "Failed to delete non-billable Solidtime time entry",
                    reply_id=reply_id,
                    error=str(exc),
                )
                return None
            await links_repo.delete_time_entry_link(int(reply_id))
        return None

    ticket_id = reply.get("ticket_id")
    if not isinstance(ticket_id, int):
        return None
    project_link = await links_repo.get_project_link(ticket_id)
    if not project_link or not project_link.get("solidtime_project_id"):
        # Push the ticket first so that we have a project to log against.
        try:
            project_link = await sync_ticket_to_project(ticket_id)
        except SolidtimeAPIError as exc:
            await links_repo.mark_time_entry_link_error(int(reply_id), str(exc))
            raise
    if not project_link or not project_link.get("solidtime_project_id"):
        return None

    org_id = str(project_link["solidtime_org_id"])
    project_id = str(project_link["solidtime_project_id"])
    ticket = await tickets_repo.get_ticket(ticket_id) or {}
    member_id = await _resolve_member_id_for_user(org_id, reply.get("author_id"))

    task_id: str | None = None
    if settings.get("labour_type_to_task") and reply.get("labour_type_name"):
        try:
            tasks = await list_tasks(org_id, project_id)
        except SolidtimeAPIError:  # pragma: no cover - non-critical
            tasks = []
        labour_name = str(reply.get("labour_type_name") or "").strip()
        for task in tasks:
            if str(task.get("name") or "").strip().lower() == labour_name.lower():
                task_id = str(task.get("id") or "")
                break
        if labour_name and not task_id:
            try:
                created_task = await create_task(
                    org_id, project_id, name=labour_name
                )
                task_id = str(created_task.get("id") or "") or None
            except SolidtimeAPIError as exc:  # pragma: no cover - non-critical
                log_warning(
                    "Failed to create Solidtime task for labour type",
                    error=str(exc),
                )

    body = reply_to_time_entry_payload(
        reply,
        ticket,
        project_id=project_id,
        member_id=member_id,
        task_id=task_id,
    )
    if body is None:
        return None
    payload_hash = _hash_payload(body)

    existing = await links_repo.get_time_entry_link(int(reply_id))
    try:
        if existing and existing.get("solidtime_time_entry_id"):
            entry_id = str(existing["solidtime_time_entry_id"])
            if existing.get("last_payload_hash") == payload_hash:
                return await links_repo.upsert_time_entry_link(
                    ticket_reply_id=int(reply_id),
                    solidtime_org_id=org_id,
                    solidtime_time_entry_id=entry_id,
                    direction=str(existing.get("direction") or "out"),
                    payload_hash=payload_hash,
                    sync_status="synced",
                )
            await update_time_entry(org_id, entry_id, body=body)
        else:
            created = await create_time_entry(org_id, body=body)
            entry_id = str(created.get("id") or "").strip()
            if not entry_id:
                raise SolidtimeAPIError(
                    "Solidtime did not return a time entry ID"
                )
    except SolidtimeAPIError as exc:
        await links_repo.mark_time_entry_link_error(int(reply_id), str(exc))
        raise
    return await links_repo.upsert_time_entry_link(
        ticket_reply_id=int(reply_id),
        solidtime_org_id=org_id,
        solidtime_time_entry_id=entry_id,
        direction="out",
        payload_hash=payload_hash,
        sync_status="synced",
    )


# ---------------------------------------------------------------------------
# Background-task scheduling helpers
# ---------------------------------------------------------------------------

def _track_background_task(task: asyncio.Task[Any]) -> None:
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


def _solidtime_monitor_target_url(settings: Mapping[str, Any] | None) -> str:
    base_url = ""
    if settings:
        base_url = str(settings.get("base_url") or "").strip()
    if base_url:
        try:
            return _normalise_base_url(base_url)
        except SolidtimeConfigurationError:
            return "solidtime://invalid-base-url"
    return "solidtime://ticket-sync"


def _ticket_sync_outcome_reason(
    *,
    settings: Mapping[str, Any] | None,
    sync_result: dict[str, Any] | None,
) -> str:
    if sync_result is not None:
        return "ticket synced"
    if not settings:
        return "Solidtime module is not configured"
    if not bool(settings.get("enabled")):
        return "Solidtime module is disabled"
    if not str(settings.get("base_url") or "").strip():
        return "Solidtime base URL is not configured"
    if not str(settings.get("api_token") or "").strip():
        return "Solidtime API token is not configured"
    if settings.get("sync_tickets_to_projects") is False:
        return "sync_tickets_to_projects is disabled"
    return "sync returned no action"


async def _record_ticket_sync_outcome(
    *,
    ticket_id: int,
    settings: Mapping[str, Any] | None,
    status: str,
    reason: str,
    error_message: str | None = None,
    sync_result: Mapping[str, Any] | None = None,
) -> None:
    target_url = _solidtime_monitor_target_url(settings)
    payload: dict[str, Any] = {
        "operation": "ticket_sync",
        "ticket_id": ticket_id,
        "status": status,
        "reason": reason,
    }
    if sync_result is not None:
        payload["result"] = sync_result
    try:
        event = await webhook_monitor.create_manual_event(
            name="solidtime.ticket.sync",
            target_url=target_url,
            payload=payload,
            headers=None,
            max_attempts=1,
            backoff_seconds=0,
        )
        if not event or event.get("id") is None:
            return
        event_id = int(event["id"])
        if status == "succeeded":
            await webhook_monitor.record_manual_success(
                event_id,
                attempt_number=1,
                response_status=200,
                response_body=reason,
                request_headers=None,
                request_body=payload,
                response_headers=None,
            )
            return
        await webhook_monitor.record_manual_failure(
            event_id,
            attempt_number=1,
            status=status,
            error_message=error_message or reason,
            response_status=None,
            response_body=reason,
            request_headers=None,
            request_body=payload,
            response_headers=None,
        )
    except Exception as exc:  # pragma: no cover - monitor safety
        log_error(
            "Failed to record Solidtime ticket sync in webhook monitor",
            ticket_id=ticket_id,
            error=str(exc),
        )


def schedule_ticket_sync(ticket_id: int) -> None:
    """Schedule a fire-and-forget push of a ticket to Solidtime."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run() -> None:
        settings_snapshot: Mapping[str, Any] | None = None
        try:
            settings_snapshot = await _load_module_settings()
            result = await sync_ticket_to_project(int(ticket_id))
            reason = _ticket_sync_outcome_reason(
                settings=settings_snapshot, sync_result=result
            )
            if result is None:
                await _record_ticket_sync_outcome(
                    ticket_id=int(ticket_id),
                    settings=settings_snapshot,
                    status="skipped",
                    reason=reason,
                    error_message=reason,
                    sync_result=None,
                )
            else:
                await _record_ticket_sync_outcome(
                    ticket_id=int(ticket_id),
                    settings=settings_snapshot,
                    status="succeeded",
                    reason=reason,
                    sync_result=result,
                )
        except SolidtimeConfigurationError as exc:
            reason = str(exc)
            log_warning(
                "Solidtime ticket sync skipped",
                ticket_id=ticket_id,
                reason=reason,
            )
            await _record_ticket_sync_outcome(
                ticket_id=int(ticket_id),
                settings=settings_snapshot,
                status="skipped",
                reason=reason,
                error_message=reason,
            )
        except SolidtimeAPIError as exc:
            log_warning(
                "Solidtime ticket sync failed",
                ticket_id=ticket_id,
                error=str(exc),
            )
            await _record_ticket_sync_outcome(
                ticket_id=int(ticket_id),
                settings=settings_snapshot,
                status="failed",
                reason="Solidtime API error",
                error_message=str(exc),
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Unexpected error during Solidtime ticket sync",
                ticket_id=ticket_id,
                error=str(exc),
            )
            await _record_ticket_sync_outcome(
                ticket_id=int(ticket_id),
                settings=settings_snapshot,
                status="error",
                reason="Unexpected error during ticket sync",
                error_message=str(exc),
            )

    _track_background_task(loop.create_task(_run()))


def schedule_reply_sync(reply_id: int) -> None:
    """Schedule a fire-and-forget push of a reply's time entry to Solidtime."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run() -> None:
        try:
            await sync_reply_to_time_entry(int(reply_id))
        except SolidtimeConfigurationError:
            pass
        except SolidtimeAPIError as exc:
            log_warning(
                "Solidtime reply sync failed",
                reply_id=reply_id,
                error=str(exc),
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Unexpected error during Solidtime reply sync",
                reply_id=reply_id,
                error=str(exc),
            )

    _track_background_task(loop.create_task(_run()))


# ---------------------------------------------------------------------------
# Inbound webhook signature verification
# ---------------------------------------------------------------------------

def verify_webhook_signature(secret: str, body: bytes, signature: str | None) -> bool:
    """Verify the HMAC-SHA256 signature carried in the inbound webhook header.

    Solidtime does not currently emit webhooks, but the verifier is present so
    that operators can configure their own forwarder (for example, a
    self-hosted relay) and trust signed payloads. Empty secrets always return
    ``False`` so that an unconfigured module never accepts unauthenticated
    posts.
    """
    if not secret or not signature:
        return False
    if signature.startswith("sha256="):
        provided = signature.split("=", 1)[1]
    else:
        provided = signature
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    try:
        return hmac.compare_digest(expected, provided.strip())
    except Exception:  # pragma: no cover - defensive
        return False


async def _sync_time_entry_from_solidtime(
    org_id: str, entry: Mapping[str, Any]
) -> bool:
    entry_id = str(entry.get("id") or "").strip()
    if not entry_id:
        return False
    payload = _time_entry_payload(entry)
    if payload is None:
        return False
    payload_hash = _hash_payload(payload)

    link = await links_repo.get_time_entry_link_by_remote(org_id, entry_id)
    if link and link.get("last_payload_hash") == payload_hash:
        return False

    minutes_spent = _time_entry_minutes(entry)
    if minutes_spent <= 0:
        return False
    billable = _time_entry_billable(entry)
    end_dt = _coerce_aware_utc(entry.get("end")) or datetime.now(timezone.utc)

    if link and link.get("ticket_reply_id"):
        reply_id = int(link["ticket_reply_id"])
        reply = await tickets_repo.get_reply_by_id(reply_id)
        if not reply:
            return False
        update_kwargs: dict[str, Any] = {}
        if int(reply.get("minutes_spent") or 0) != minutes_spent:
            update_kwargs["minutes_spent"] = minutes_spent
        if _coerce_bool(reply.get("is_billable")) != billable:
            update_kwargs["is_billable"] = billable
        if update_kwargs:
            await tickets_repo.update_reply(reply_id, **update_kwargs)
        await links_repo.upsert_time_entry_link(
            ticket_reply_id=reply_id,
            solidtime_org_id=org_id,
            solidtime_time_entry_id=entry_id,
            direction=str(link.get("direction") or "in"),
            payload_hash=payload_hash,
            sync_status="synced",
        )
        return bool(update_kwargs)

    project_id = _time_entry_project_id(entry)
    if not project_id:
        return False
    project_link = await links_repo.get_project_link_by_remote(org_id, project_id)
    if not project_link or not project_link.get("ticket_id"):
        return False
    reply = await tickets_repo.create_reply(
        ticket_id=int(project_link["ticket_id"]),
        author_id=None,
        body=_time_entry_description(entry),
        is_internal=True,
        minutes_spent=minutes_spent,
        is_billable=billable,
        created_at=end_dt,
    )
    reply_id = reply.get("id")
    if not isinstance(reply_id, int) or reply_id <= 0:
        return False
    await links_repo.upsert_time_entry_link(
        ticket_reply_id=reply_id,
        solidtime_org_id=org_id,
        solidtime_time_entry_id=entry_id,
        direction="in",
        payload_hash=payload_hash,
        sync_status="synced",
    )
    return True


# ---------------------------------------------------------------------------
# Inbound reconciler (poll-based)
# ---------------------------------------------------------------------------

async def reconcile_once() -> dict[str, Any]:
    """Reconcile state between Solidtime and MyPortal.

    Returns a small status dict describing how many entities were inspected
    and updated. The function is safe to call when the module is disabled.
    """
    summary: dict[str, Any] = {
        "status": "skipped",
        "tickets_pushed": 0,
        "projects_pulled": 0,
        "time_entries_pulled": 0,
        "errors": 0,
    }
    try:
        settings = await _get_effective_settings()
    except SolidtimeConfigurationError as exc:
        summary["reason"] = str(exc)
        log_warning("Solidtime reconcile skipped", reason=str(exc))
        return summary

    org_id = settings["organization_id"]
    if not org_id:
        reason = "Solidtime organization_id is not configured"
        summary["reason"] = reason
        log_warning("Solidtime reconcile skipped", reason=reason)
        return summary

    summary["status"] = "ok"

    # Outbound: push open tickets without a Solidtime project link to Solidtime.
    if settings.get("sync_tickets_to_projects"):
        unsynced_ids = await links_repo.list_unsynced_ticket_ids()
        for ticket_id in unsynced_ids:
            try:
                await sync_ticket_to_project(ticket_id)
                summary["tickets_pushed"] += 1
            except SolidtimeConfigurationError:
                pass
            except SolidtimeAPIError as exc:
                summary["status"] = "error"
                summary["errors"] += 1
                log_warning(
                    "Solidtime ticket push failed during reconcile",
                    ticket_id=ticket_id,
                    error=str(exc),
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                summary["status"] = "error"
                summary["errors"] += 1
                log_warning(
                    "Solidtime ticket push failed unexpectedly",
                    ticket_id=ticket_id,
                    error=str(exc),
                )

    if settings.get("sync_projects_to_tickets") or settings.get(
        "sync_time_entries_from_solidtime"
    ):
        try:
            projects = await list_projects(org_id)
        except SolidtimeAPIError as exc:
            summary["status"] = "error"
            summary["errors"] += 1
            log_warning("Solidtime project pull failed", error=str(exc))
            projects = []
        summary["projects_pulled"] = len(projects)

    if settings.get("sync_time_entries_from_solidtime"):
        try:
            entries = await list_time_entries(org_id)
        except SolidtimeAPIError as exc:
            summary["status"] = "error"
            summary["errors"] += 1
            log_warning("Solidtime time entry pull failed", error=str(exc))
            entries = []
        summary["time_entries_pulled"] = len(entries)
        for entry in entries:
            try:
                await _sync_time_entry_from_solidtime(org_id, entry)
            except Exception as exc:  # pragma: no cover - defensive logging
                summary["status"] = "error"
                summary["errors"] += 1
                log_warning(
                    "Solidtime time entry reconciliation failed",
                    entry_id=str(entry.get("id") or ""),
                    error=str(exc),
                )

    return summary


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

async def get_ticket_links(ticket_id: int) -> dict[str, Any]:
    """Return the Solidtime URLs to surface on the ticket detail page.

    Always returns a dict so callers can use ``.get()`` safely. When the
    module is disabled or no link exists, ``project_url`` and ``timer_url``
    are empty strings.
    """
    result: dict[str, Any] = {
        "enabled": False,
        "project_url": "",
        "timer_url": "",
        "project_id": "",
        "organization_id": "",
        "last_synced_at": None,
        "sync_status": "",
    }
    try:
        settings = await _load_module_settings() or {}
    except RuntimeError:
        return result
    if not settings.get("enabled") or not settings.get("base_url"):
        return result

    base_url = str(settings.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        return result
    # The host (without the API path) is used for human-facing links.
    if base_url.endswith("/api/v1"):
        host_url = base_url[: -len("/api/v1")]
    else:
        host_url = base_url

    org_id = str(settings.get("organization_id") or "").strip()
    result["enabled"] = True
    result["organization_id"] = org_id
    # Always provide the timer URL — clicking it opens Solidtime at the
    # timer page. When a project link exists we include a project hint.
    timer_url = f"{host_url}/time"
    project_link = await links_repo.get_project_link(int(ticket_id))
    if project_link and project_link.get("solidtime_project_id"):
        project_id = str(project_link["solidtime_project_id"])
        result["project_id"] = project_id
        result["last_synced_at"] = project_link.get("last_synced_at")
        result["sync_status"] = str(project_link.get("sync_status") or "")
        result["project_url"] = f"{host_url}/projects/{project_id}"
        timer_url = f"{host_url}/time?project={project_id}"
    result["timer_url"] = timer_url
    return result
