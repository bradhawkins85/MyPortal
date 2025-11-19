from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from app.repositories import service_status as service_status_repo
from app.services import tag_generator

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
