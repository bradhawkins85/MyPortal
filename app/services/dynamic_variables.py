from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.repositories import assets as assets_repo


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


async def build_dynamic_token_map(
    tokens: Iterable[str],
    context: Mapping[str, Any] | None,
    *,
    base_tokens: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    requests = _extract_active_asset_requests(tokens)
    if not requests:
        return {}

    company_id = _extract_company_id(context, base_tokens)
    now = _utcnow()

    unique_durations: list[int | None] = []
    for duration in requests.values():
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

    return {token: counts[duration] for token, duration in requests.items()}
