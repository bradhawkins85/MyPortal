from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.repositories import assets as assets_repo
from app.repositories import asset_custom_fields as asset_custom_fields_repo
from app.repositories import issues as issues_repo


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_int(value: Any) -> int | None:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return None
    if candidate < 0:
        return None
    return candidate


def _extract_company_id(
    context: Mapping[str, Any] | None,
    base_tokens: Mapping[str, Any] | None,
) -> int | None:
    if isinstance(context, Mapping):
        direct = _coerce_int(context.get("company_id"))
        if direct is not None:
            return direct
        company = context.get("company")
        if isinstance(company, Mapping):
            company_id = _coerce_int(company.get("id") or company.get("company_id"))
            if company_id is not None:
                return company_id
        ticket = context.get("ticket")
        if isinstance(ticket, Mapping):
            ticket_company = _coerce_int(ticket.get("company_id") or ticket.get("companyId"))
            if ticket_company is not None:
                return ticket_company
            ticket_company_entry = ticket.get("company")
            if isinstance(ticket_company_entry, Mapping):
                ticket_company_id = _coerce_int(
                    ticket_company_entry.get("id") or ticket_company_entry.get("company_id")
                )
                if ticket_company_id is not None:
                    return ticket_company_id
    if isinstance(base_tokens, Mapping):
        for key in ("TICKET_COMPANY_ID", "COMPANY_ID"):
            value = base_tokens.get(key)
            company_id = _coerce_int(value)
            if company_id is not None:
                return company_id
    return None


def _extract_active_asset_requests(tokens: Iterable[str]) -> dict[str, int | None]:
    requests: dict[str, int | None] = {}
    for token in tokens:
        if not token:
            continue
        upper = token.upper()
        if upper == "ACTIVE_ASSETS":
            requests[token] = None
            continue
        if not upper.startswith("ACTIVE_ASSETS:"):
            continue
        parts = token.split(":", 1)
        if len(parts) != 2:
            continue
        suffix = parts[1].strip()
        try:
            days = int(suffix)
        except (TypeError, ValueError):
            continue
        if days < 0:
            continue
        requests[token] = days
    return requests


def _extract_asset_custom_field_count_requests(tokens: Iterable[str]) -> dict[str, str]:
    """Extract count:asset:field-name tokens.
    
    Returns a dict mapping the full token to the field name.
    For example: {"count:asset:bitdefender": "bitdefender"}
    """
    requests: dict[str, str] = {}
    for token in tokens:
        if not token:
            continue
        # Support both lowercase and uppercase variants
        lower = token.lower()
        if not lower.startswith("count:asset:"):
            continue
        parts = token.split(":", 2)
        if len(parts) != 3:
            continue
        field_name = parts[2].strip()
        if field_name:
            requests[token] = field_name
    return requests


def _extract_asset_custom_field_list_requests(tokens: Iterable[str]) -> dict[str, str]:
    """Extract list:asset:field-name tokens.
    
    Returns a dict mapping the full token to the field name.
    For example: {"list:asset:bitdefender": "bitdefender"}
    """
    requests: dict[str, str] = {}
    for token in tokens:
        if not token:
            continue
        # Support both lowercase and uppercase variants
        lower = token.lower()
        if not lower.startswith("list:asset:"):
            continue
        parts = token.split(":", 2)
        if len(parts) != 3:
            continue
        field_name = parts[2].strip()
        if field_name:
            requests[token] = field_name
    return requests


def _extract_issue_count_requests(tokens: Iterable[str]) -> dict[str, str]:
    """Extract count:issue:slug tokens.
    
    Returns a dict mapping the full token to the issue slug.
    For example: {"count:issue:network-outage": "network-outage"}
    """
    requests: dict[str, str] = {}
    for token in tokens:
        if not token:
            continue
        # Support both lowercase and uppercase variants
        lower = token.lower()
        if not lower.startswith("count:issue:"):
            continue
        parts = token.split(":", 2)
        if len(parts) != 3:
            continue
        issue_slug = parts[2].strip()
        if issue_slug:
            requests[token] = issue_slug
    return requests


def _extract_issue_list_requests(tokens: Iterable[str]) -> dict[str, str]:
    """Extract list:issue:slug tokens.
    
    Returns a dict mapping the full token to the issue slug.
    For example: {"list:issue:network-outage": "network-outage"}
    """
    requests: dict[str, str] = {}
    for token in tokens:
        if not token:
            continue
        # Support both lowercase and uppercase variants
        lower = token.lower()
        if not lower.startswith("list:issue:"):
            continue
        parts = token.split(":", 2)
        if len(parts) != 3:
            continue
        issue_slug = parts[2].strip()
        if issue_slug:
            requests[token] = issue_slug
    return requests


async def build_dynamic_token_map(
    tokens: Iterable[str],
    context: Mapping[str, Any] | None,
    *,
    base_tokens: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    active_asset_requests = _extract_active_asset_requests(tokens)
    custom_field_requests = _extract_asset_custom_field_count_requests(tokens)
    custom_field_list_requests = _extract_asset_custom_field_list_requests(tokens)
    issue_count_requests = _extract_issue_count_requests(tokens)
    issue_list_requests = _extract_issue_list_requests(tokens)
    
    if not active_asset_requests and not custom_field_requests and not custom_field_list_requests and not issue_count_requests and not issue_list_requests:
        return {}

    company_id = _extract_company_id(context, base_tokens)
    result: dict[str, str] = {}
    
    # Handle active asset counts
    if active_asset_requests:
        now = _utcnow()
        unique_durations: list[int | None] = []
        for duration in active_asset_requests.values():
            if duration not in unique_durations:
                unique_durations.append(duration)

        counts: dict[int | None, str] = {}
        for duration in unique_durations:
            if duration is None:
                since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            else:
                since = now - timedelta(days=duration)
            count = await assets_repo.count_active_assets(company_id=company_id, since=since)
            counts[duration] = str(count)

        result.update({token: counts[duration] for token, duration in active_asset_requests.items()})
    
    # Handle custom field asset counts
    if custom_field_requests:
        for token, field_name in custom_field_requests.items():
            count = await asset_custom_fields_repo.count_assets_by_custom_field(
                company_id=company_id,
                field_name=field_name,
                field_value=True,
            )
            result[token] = str(count)
    
    # Handle custom field asset lists
    if custom_field_list_requests:
        for token, field_name in custom_field_list_requests.items():
            assets = await asset_custom_fields_repo.list_assets_by_custom_field(
                company_id=company_id,
                field_name=field_name,
                field_value=True,
            )
            result[token] = ", ".join(assets)
    
    # Handle issue asset counts
    if issue_count_requests:
        for token, issue_slug in issue_count_requests.items():
            count = await issues_repo.count_assets_by_issue_slug(
                company_id=company_id,
                issue_slug=issue_slug,
            )
            result[token] = str(count)
    
    # Handle issue asset lists
    if issue_list_requests:
        for token, issue_slug in issue_list_requests.items():
            assets = await issues_repo.list_assets_by_issue_slug(
                company_id=company_id,
                issue_slug=issue_slug,
            )
            result[token] = ", ".join(assets)
    
    return result
