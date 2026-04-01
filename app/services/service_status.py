from __future__ import annotations

import ipaddress
import json
import re
import socket
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.logging import log_error, log_warning
from app.repositories import service_status as service_status_repo
from app.services import modules as modules_service
from app.services import tag_generator

_AI_LOOKUP_MAX_URL_CONTENT = 8000
_SSRF_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

DEFAULT_STATUS = "operational"

STATUS_DEFINITIONS: list[dict[str, str]] = [
    {
        "value": "operational",
        "label": "Operational",
        "description": "Service is working normally",
        "variant": "status--operational",
    },
    {
        "value": "maintenance",
        "label": "Maintenance",
        "description": "Maintenance is in progress",
        "variant": "status--maintenance",
    },
    {
        "value": "degraded",
        "label": "Degraded",
        "description": "Performance issues detected",
        "variant": "status--degraded",
    },
    {
        "value": "partial_outage",
        "label": "Partial outage",
        "description": "Service disruption affecting some users",
        "variant": "status--partial_outage",
    },
    {
        "value": "outage",
        "label": "Major outage",
        "description": "Service is unavailable",
        "variant": "status--outage",
    },
]

_STATUS_LOOKUP = {entry["value"]: entry for entry in STATUS_DEFINITIONS}


def normalise_company_ids(company_ids: Sequence[int | str] | None) -> list[int]:
    if not company_ids:
        return []
    normalised: list[int] = []
    seen: set[int] = set()
    for raw in company_ids:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        normalised.append(value)
    return normalised


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalise_status(value: Any) -> str:
    if value is None:
        return DEFAULT_STATUS
    status = str(value).strip().lower()
    if status not in _STATUS_LOOKUP:
        raise ValueError("Invalid status selection")
    return status


def describe_status(value: str) -> dict[str, str]:
    return _STATUS_LOOKUP.get(value, _STATUS_LOOKUP[DEFAULT_STATUS])


def _parse_tags(value: Any) -> list[str]:
    """Parse tags from a comma-separated string or list."""
    if not value:
        return []
    
    if isinstance(value, list):
        # Already a list, clean each tag
        tags = []
        for tag in value:
            cleaned = str(tag).strip().lower() if tag else ""
            if cleaned and len(cleaned) <= 50:
                tags.append(cleaned)
        return tags
    
    # Parse as comma-separated string
    tags = []
    for tag in str(value).split(","):
        cleaned = tag.strip().lower()
        if cleaned and len(cleaned) <= 50:
            tags.append(cleaned)
    return tags


def _serialize_tags(tags: list[str]) -> str:
    """Convert a list of tags to a comma-separated string for storage."""
    if not tags:
        return ""
    return ", ".join(str(tag).strip() for tag in tags if tag and str(tag).strip())


_AI_LOOKUP_FREQUENCY_FIELDS = (
    "ai_lookup_frequency_operational",
    "ai_lookup_frequency_degraded",
    "ai_lookup_frequency_partial_outage",
    "ai_lookup_frequency_outage",
    "ai_lookup_frequency_maintenance",
)

_AI_LOOKUP_FREQUENCY_DEFAULTS: dict[str, int] = {
    "ai_lookup_frequency_operational": 60,
    "ai_lookup_frequency_degraded": 15,
    "ai_lookup_frequency_partial_outage": 10,
    "ai_lookup_frequency_outage": 5,
    "ai_lookup_frequency_maintenance": 60,
}


def _parse_ai_lookup_frequency(value: Any, *, default: int) -> int:
    try:
        result = int(value)
        return result if result > 0 else default
    except (TypeError, ValueError):
        return default


def _extract_ai_lookup_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Extract and normalise AI lookup fields from a payload mapping."""
    fields: dict[str, Any] = {}
    if "ai_lookup_enabled" in payload:
        fields["ai_lookup_enabled"] = 1 if bool(payload.get("ai_lookup_enabled")) else 0
    if "ai_lookup_url" in payload:
        fields["ai_lookup_url"] = _clean_text(payload.get("ai_lookup_url"))
    if "ai_lookup_prompt" in payload:
        fields["ai_lookup_prompt"] = _clean_text(payload.get("ai_lookup_prompt"))
    if "ai_lookup_model_override" in payload:
        fields["ai_lookup_model_override"] = _clean_text(payload.get("ai_lookup_model_override"))
    for freq_field in _AI_LOOKUP_FREQUENCY_FIELDS:
        if freq_field in payload:
            fields[freq_field] = _parse_ai_lookup_frequency(
                payload.get(freq_field),
                default=_AI_LOOKUP_FREQUENCY_DEFAULTS[freq_field],
            )
    return fields


async def list_services(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    return await service_status_repo.list_services(include_inactive=include_inactive)


async def list_services_for_company(
    company_id: int | None,
    *,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    services = await list_services(include_inactive=include_inactive)
    if company_id is None:
        return [service for service in services if not service.get("company_ids")]
    allowed: list[dict[str, Any]] = []
    for service in services:
        company_ids = service.get("company_ids") or []
        if not company_ids or company_id in company_ids:
            allowed.append(service)
    return allowed


async def get_service(service_id: int) -> dict[str, Any] | None:
    return await service_status_repo.get_service(service_id)


async def create_service(
    payload: Mapping[str, Any],
    *,
    company_ids: Sequence[int | str] | None = None,
    updated_by: int | None = None,
    generate_tags: bool = True,
) -> dict[str, Any]:
    name = _clean_text(payload.get("name")) if payload else None
    if not name:
        raise ValueError("Service name is required")
    description = _clean_text(payload.get("description") if payload else None)
    status_message = _clean_text(payload.get("status_message") if payload else None)
    status = _normalise_status((payload or {}).get("status"))
    display_order = _parse_int((payload or {}).get("display_order"), default=0)
    is_active = bool((payload or {}).get("is_active", True))
    
    # Handle tags
    tags_input = (payload or {}).get("tags")
    if tags_input:
        # Use provided tags
        tags = _parse_tags(tags_input)
    elif generate_tags:
        # Generate tags using AI
        tags = await tag_generator.generate_tags_for_service(name, description)
    else:
        tags = []
    
    repo_payload: dict[str, Any] = {
        "name": name,
        "description": description,
        "status": status,
        "status_message": status_message,
        "display_order": display_order,
        "is_active": 1 if is_active else 0,
        "tags": _serialize_tags(tags),
    }
    if updated_by:
        repo_payload["updated_by"] = updated_by
    # Include AI lookup fields
    ai_lookup_fields = _extract_ai_lookup_fields(payload or {})
    repo_payload.update(ai_lookup_fields)
    return await service_status_repo.create_service(
        repo_payload,
        company_ids=normalise_company_ids(company_ids),
    )


async def update_service(
    service_id: int,
    payload: Mapping[str, Any],
    *,
    company_ids: Sequence[int | str] | None = None,
    updated_by: int | None = None,
) -> dict[str, Any]:
    if not payload and company_ids is None:
        existing = await get_service(service_id)
        if not existing:
            raise ValueError("Service not found")
        return existing
    updates: dict[str, Any] = {}
    if "name" in payload:
        name = _clean_text(payload.get("name"))
        if not name:
            raise ValueError("Service name is required")
        updates["name"] = name
    if "description" in payload:
        updates["description"] = _clean_text(payload.get("description"))
    if "status" in payload:
        updates["status"] = _normalise_status(payload.get("status"))
    if "status_message" in payload:
        updates["status_message"] = _clean_text(payload.get("status_message"))
    if "display_order" in payload:
        updates["display_order"] = _parse_int(payload.get("display_order"), default=0)
    if "is_active" in payload:
        updates["is_active"] = 1 if bool(payload.get("is_active")) else 0
    if "tags" in payload:
        tags = _parse_tags(payload.get("tags"))
        updates["tags"] = _serialize_tags(tags)
    # AI lookup fields
    updates.update(_extract_ai_lookup_fields(payload))
    if updated_by:
        updates["updated_by"] = updated_by
    return await service_status_repo.update_service(
        service_id,
        updates,
        company_ids=None if company_ids is None else normalise_company_ids(company_ids),
    )


async def update_service_status(
    service_id: int,
    *,
    status: str,
    status_message: str | None = None,
    updated_by: int | None = None,
) -> dict[str, Any]:
    updates = {
        "status": _normalise_status(status),
        "status_message": _clean_text(status_message),
    }
    if updated_by:
        updates["updated_by"] = updated_by
    return await service_status_repo.update_service(service_id, updates)


async def delete_service(service_id: int) -> None:
    await service_status_repo.delete_service(service_id)


async def refresh_service_tags(service_id: int) -> dict[str, Any]:
    """Regenerate tags for a service using AI."""
    service = await get_service(service_id)
    if not service:
        raise ValueError("Service not found")
    
    name = service.get("name")
    description = service.get("description")
    
    if not name:
        raise ValueError("Service name is required for tag generation")
    
    # Generate new tags
    tags = await tag_generator.generate_tags_for_service(name, description)
    
    # Update the service with new tags
    updates = {"tags": _serialize_tags(tags)}
    return await service_status_repo.update_service(service_id, updates)


def summarise_services(services: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    summary = Counter()
    total = 0
    for service in services:
        total += 1
        status = str(service.get("status") or DEFAULT_STATUS)
        if status not in _STATUS_LOOKUP:
            status = DEFAULT_STATUS
        summary[status] += 1
    return {
        "total": total,
        "by_status": {value: summary.get(value, 0) for value in _STATUS_LOOKUP},
    }


async def find_relevant_services_for_ticket(
    ticket_ai_tags: list[str],
    company_id: int | None,
) -> list[dict[str, Any]]:
    """
    Find services that have tags matching the ticket's AI tags.
    Only returns services that the user has permission to see based on company_id.
    
    Args:
        ticket_ai_tags: List of AI tags from the ticket
        company_id: The company ID to filter services by (for permission checking)
    
    Returns:
        List of relevant services with matching tag count
    """
    if not ticket_ai_tags:
        return []
    
    # Get all services visible to this company
    services = await list_services_for_company(company_id, include_inactive=False)
    
    # Filter services by matching tags
    relevant_services = []
    for service in services:
        service_tags_raw = service.get("tags") or []
        
        # Parse service tags if they're stored as a string
        if isinstance(service_tags_raw, str):
            service_tags = [tag.strip().lower() for tag in service_tags_raw.split(",") if tag.strip()]
        elif isinstance(service_tags_raw, list):
            service_tags = [str(tag).strip().lower() for tag in service_tags_raw if tag]
        else:
            service_tags = []
        
        # Count matching tags
        matching_tags = set(ticket_ai_tags).intersection(set(service_tags))
        
        if matching_tags:
            # Add matching_tags_count to service info
            service_with_matches = {
                **service,
                "matching_tags_count": len(matching_tags),
                "matching_tags": list(matching_tags),
            }
            relevant_services.append(service_with_matches)
    
    # Sort by number of matching tags (descending) and then by name
    relevant_services.sort(key=lambda s: (-s["matching_tags_count"], str(s.get("name", "")).lower()))
    
    return relevant_services


def _frequency_for_status(service: Mapping[str, Any]) -> int:
    """Return the AI lookup frequency (in minutes) based on the current service status."""
    status = str(service.get("status") or DEFAULT_STATUS)
    freq_map = {
        "operational": service.get("ai_lookup_frequency_operational"),
        "degraded": service.get("ai_lookup_frequency_degraded"),
        "partial_outage": service.get("ai_lookup_frequency_partial_outage"),
        "outage": service.get("ai_lookup_frequency_outage"),
        "maintenance": service.get("ai_lookup_frequency_maintenance"),
    }
    raw = freq_map.get(status)
    try:
        minutes = int(raw)
        return minutes if minutes > 0 else _AI_LOOKUP_FREQUENCY_DEFAULTS.get(status, 60)
    except (TypeError, ValueError):
        return _AI_LOOKUP_FREQUENCY_DEFAULTS.get(status, 60)


def _is_lookup_due(service: Mapping[str, Any]) -> bool:
    """Return True if the service AI lookup is due based on frequency and last checked time."""
    last_checked = service.get("ai_lookup_last_checked_at")
    if last_checked is None:
        return True
    frequency_minutes = _frequency_for_status(service)
    now = datetime.now(timezone.utc)
    if isinstance(last_checked, datetime):
        if last_checked.tzinfo is None:
            last_checked = last_checked.replace(tzinfo=timezone.utc)
        elapsed_minutes = (now - last_checked).total_seconds() / 60
    else:
        # Fallback: treat as due
        return True
    return elapsed_minutes >= frequency_minutes


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Try to extract a JSON object from *text*.

    Handles:
    * Pure JSON strings
    * JSON wrapped in markdown fenced code blocks (```json ... ```)
    * JSON embedded in surrounding prose (first ``{`` … last ``}``)

    Each extraction attempt is validated with ``json.loads`` so stray braces or
    non-JSON code blocks are safely rejected.
    """
    stripped = text.strip()

    # 1. Direct parse
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Strip markdown fenced code blocks
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
    if md_match:
        try:
            data = json.loads(md_match.group(1).strip())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. Find the first { … last } substring
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(stripped[start : end + 1])
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _parse_ai_status_response(text: str) -> tuple[str | None, str | None]:
    """
    Parse an Ollama response into (status, message).

    The model is expected to return a JSON object like:
    {"status": "operational", "message": "All systems normal"}

    Handles responses where the JSON is wrapped in markdown code blocks or
    surrounded by explanatory prose (common with LLM outputs).

    Falls back to keyword scanning if JSON is not found.
    """
    if not text:
        return None, None

    # Try JSON extraction (handles plain, markdown-wrapped, and embedded JSON)
    data = _extract_json_object(text)
    if data is not None:
        status_raw = str(data.get("status") or "").strip().lower()
        message_raw = str(data.get("message") or "").strip() or None
        if status_raw in _STATUS_LOOKUP:
            return status_raw, message_raw

    # Keyword scan fallback (order matters – most specific first)
    lower_text = text.lower()
    for candidate in ("partial_outage", "outage", "degraded", "maintenance", "operational"):
        if candidate.replace("_", " ") in lower_text or candidate in lower_text:
            return candidate, None

    return None, None


def _validate_lookup_url(url: str) -> str:
    """
    Validate the AI lookup URL to prevent SSRF attacks.

    Raises ValueError if the URL resolves to a private/loopback address or uses a
    non-HTTP(S) scheme.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Lookup URL must use http or https scheme")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Lookup URL must include a hostname")
    try:
        addr = socket.getaddrinfo(hostname, None, flags=socket.AI_NUMERICSERV)
    except socket.gaierror:
        raise ValueError(f"Unable to resolve hostname: {hostname}")
    for _family, _type, _proto, _canonname, sockaddr in addr:
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for blocked in _SSRF_BLOCKED_NETWORKS:
            if ip_obj in blocked:
                raise ValueError(f"Lookup URL resolves to a private/internal address ({ip_obj})")
    return url


def _sanitize_url_content(content: str) -> str:
    """Strip content to a safe size and remove control characters that could cause prompt injection."""
    safe = content[:_AI_LOOKUP_MAX_URL_CONTENT]
    # Remove null bytes and ASCII control characters except whitespace
    safe = "".join(ch for ch in safe if ch >= " " or ch in "\t\n\r")
    return safe


async def run_ai_lookup_for_service(service_id: int) -> dict[str, Any]:
    """
    Perform an AI lookup for a single service.

    Fetches the configured URL, passes the content together with the prompt to
    the configured Ollama server, and updates the service status from the response.

    Returns a dict with keys: service_id, status, message, changed, error.
    """
    service = await get_service(service_id)
    if not service:
        return {"service_id": service_id, "error": "Service not found", "changed": False}
    if not service.get("ai_lookup_enabled"):
        return {"service_id": service_id, "error": "AI lookup not enabled", "changed": False}

    lookup_url = _clean_text(service.get("ai_lookup_url"))
    prompt_template = _clean_text(service.get("ai_lookup_prompt"))
    model_override = _clean_text(service.get("ai_lookup_model_override"))

    if not lookup_url:
        return {"service_id": service_id, "error": "No lookup URL configured", "changed": False}
    if not prompt_template:
        return {"service_id": service_id, "error": "No AI prompt configured", "changed": False}

    # Validate URL to prevent SSRF
    try:
        _validate_lookup_url(lookup_url)
    except ValueError as exc:
        return {"service_id": service_id, "error": f"Invalid lookup URL: {exc}", "changed": False}

    # Fetch the URL content
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(lookup_url)
        response.raise_for_status()
        url_content = _sanitize_url_content(response.text)
    except Exception as exc:
        return {"service_id": service_id, "error": f"URL fetch failed: {exc}", "changed": False}

    # Build the full prompt
    full_prompt = (
        f"{prompt_template}\n\n"
        f"URL: {lookup_url}\n"
        f"Content:\n{url_content}\n\n"
        f"Respond with a JSON object containing exactly two keys: "
        f'"status" (one of: operational, degraded, partial_outage, outage, maintenance) '
        f'and "message" (a short human-readable description of the current state).'
    )

    now_utc = datetime.now(timezone.utc)
    ai_response_text: str | None = None

    ollama_payload: dict[str, Any] = {"prompt": full_prompt, "format": "json"}
    if model_override:
        ollama_payload["model"] = model_override

    try:
        module_result = await modules_service.trigger_module(
            "ollama", ollama_payload, background=False
        )
    except ValueError as exc:
        # trigger_module raises ValueError when the module is not configured at all
        return {"service_id": service_id, "error": f"Ollama module not configured: {exc}", "changed": False}

    module_status = str(module_result.get("status") or "")
    if module_status == "skipped":
        return {"service_id": service_id, "error": "Ollama module not enabled", "changed": False}
    if module_status != "succeeded":
        last_error = module_result.get("last_error") or module_result.get("error") or module_status
        await service_status_repo.update_service(
            service_id,
            {"ai_lookup_last_checked_at": now_utc},
        )
        return {"service_id": service_id, "error": f"Ollama request failed: {last_error}", "changed": False}

    response_data = module_result.get("response")
    # response_data is typically a parsed dict from the Ollama JSON response body.
    # The text answer lives in response_data["response"]. A plain string fallback
    # handles any unexpected non-dict payloads.
    if isinstance(response_data, Mapping):
        ai_response_text = str(response_data.get("response") or "").strip()
    else:
        ai_response_text = str(response_data or "").strip()

    derived_status, derived_message = _parse_ai_status_response(ai_response_text or "")

    if derived_status is None:
        log_warning(
            "AI lookup could not determine service status from Ollama response",
            service_id=service_id,
            response_preview=ai_response_text[:200] if ai_response_text else "(empty)",
        )
        await service_status_repo.update_service(
            service_id,
            {"ai_lookup_last_checked_at": now_utc},
        )
        return {
            "service_id": service_id,
            "error": "AI response could not be interpreted as a valid service status",
            "changed": False,
        }

    updates: dict[str, Any] = {
        "ai_lookup_last_checked_at": now_utc,
        "ai_lookup_last_status": derived_status,
        "ai_lookup_last_message": derived_message,
    }

    changed = False
    if derived_status and derived_status != service.get("status"):
        updates["status"] = derived_status
        updates["status_message"] = derived_message
        changed = True
    elif derived_message and derived_message != service.get("status_message"):
        updates["status_message"] = derived_message
        changed = True

    await service_status_repo.update_service(service_id, updates)

    return {
        "service_id": service_id,
        "status": derived_status,
        "message": derived_message,
        "changed": changed,
        "error": None,
    }


async def run_ai_lookup_for_all_services() -> dict[str, Any]:
    """
    Check all AI-lookup-enabled services and run lookups for those that are due.

    Returns a summary dict with keys: checked, changed, skipped, errors.
    """
    services = await service_status_repo.list_services_due_for_ai_lookup()
    checked = 0
    changed = 0
    skipped = 0
    errors = 0

    for service in services:
        if not _is_lookup_due(service):
            skipped += 1
            continue
        service_id = service.get("id")
        if service_id is None:
            errors += 1
            continue
        try:
            result = await run_ai_lookup_for_service(int(service_id))
            checked += 1
            if result.get("changed"):
                changed += 1
            if result.get("error"):
                errors += 1
                log_warning(
                    "Service status AI lookup error",
                    service_id=service_id,
                    error=result["error"],
                )
        except Exception as exc:
            errors += 1
            log_error(
                "Service status AI lookup exception",
                service_id=service_id,
                error=str(exc),
            )

    return {"checked": checked, "changed": changed, "skipped": skipped, "errors": errors}
