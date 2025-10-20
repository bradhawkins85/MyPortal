from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.logging import log_error, log_info
from app.repositories import companies as company_repo
from app.repositories import tickets as tickets_repo
from app.services import syncro

_ALLOWED_PRIORITIES = {"urgent", "high", "normal", "low"}
_ALLOWED_STATUSES = {"open", "in_progress", "pending", "resolved", "closed"}
_DEFAULT_PRIORITY = "normal"
_DEFAULT_STATUS = "open"

_DEFAULT_RATE_LIMITER = syncro.AsyncRateLimiter(limit=180, interval=60.0)


@dataclass(slots=True)
class TicketImportSummary:
    mode: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0

    def record(self, outcome: str) -> None:
        if outcome == "created":
            self.created += 1
        elif outcome == "updated":
            self.updated += 1
        else:
            self.skipped += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
        }


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalise_status(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return _DEFAULT_STATUS
    normalized = text.lower().replace(" ", "_")
    if normalized in _ALLOWED_STATUSES:
        return normalized
    if "progress" in normalized:
        return "in_progress"
    if "pend" in normalized or "wait" in normalized:
        return "pending"
    if "resolv" in normalized or "complete" in normalized:
        return "resolved"
    if "clos" in normalized:
        return "closed"
    return _DEFAULT_STATUS


def _normalise_priority(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return _DEFAULT_PRIORITY
    normalized = text.lower().replace(" ", "_")
    if normalized in _ALLOWED_PRIORITIES:
        return normalized
    if "emer" in normalized or "crit" in normalized:
        return "urgent"
    if "high" in normalized:
        return "high"
    if "low" in normalized:
        return "low"
    return _DEFAULT_PRIORITY


def _parse_datetime(value: Any) -> datetime | None:
    if value in {None, "", 0}:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _extract_description(ticket: dict[str, Any]) -> str | None:
    for key in ("problem", "description", "issue", "body", "notes"):
        candidate = _clean_text(ticket.get(key))
        if candidate:
            return candidate
    return None


async def _resolve_company_id(ticket: dict[str, Any]) -> int | None:
    syncro_ids: list[str] = []
    for key in ("customer_id", "customerId", "customerid", "client_id"):
        value = ticket.get(key)
        if value is not None:
            syncro_ids.append(str(value))
    customer = ticket.get("customer")
    if isinstance(customer, dict):
        for key in ("id", "customer_id"):
            value = customer.get(key)
            if value is not None:
                syncro_ids.append(str(value))
    for syncro_id in syncro_ids:
        try:
            company = await company_repo.get_company_by_syncro_id(syncro_id)
        except RuntimeError as exc:  # pragma: no cover - defensive logging
            log_error("Failed to resolve company from Syncro ID", syncro_id=syncro_id, error=str(exc))
            continue
        if company and company.get("id") is not None:
            try:
                return int(company["id"])
            except (TypeError, ValueError):
                continue
    return None


async def _upsert_ticket(
    ticket: dict[str, Any],
) -> str:
    syncro_id = ticket.get("id")
    if syncro_id is None:
        return "skipped"
    external_reference = str(syncro_id)
    subject = _clean_text(ticket.get("subject") or ticket.get("title") or ticket.get("summary"))
    if not subject:
        subject = f"Syncro Ticket {external_reference}"
    description = _extract_description(ticket)
    status = _normalise_status(ticket.get("status_name") or ticket.get("status"))
    priority = _normalise_priority(ticket.get("priority"))
    category = _clean_text(ticket.get("type") or ticket.get("category"))

    created_at = _parse_datetime(
        ticket.get("created_at")
        or ticket.get("created_on")
        or ticket.get("created")
        or ticket.get("created_at_utc")
    )
    updated_at = _parse_datetime(
        ticket.get("updated_at")
        or ticket.get("updated_on")
        or ticket.get("updated")
        or ticket.get("updated_at_utc")
    )
    closed_at = _parse_datetime(
        ticket.get("resolved_at")
        or ticket.get("closed_at")
        or ticket.get("completed_at")
        or ticket.get("date_resolved")
    )
    if closed_at and status not in {"resolved", "closed"}:
        status = "resolved"

    company_id = await _resolve_company_id(ticket)

    existing = await tickets_repo.get_ticket_by_external_reference(external_reference)
    description_value: str | None = description or None
    category_value: str | None = category or None

    if existing:
        updates: dict[str, Any] = {
            "subject": subject,
            "description": description_value,
            "status": status,
            "priority": priority,
        }
        if category_value is not None:
            updates["category"] = category_value
        if company_id is not None:
            updates["company_id"] = company_id
        if closed_at is not None:
            updates["closed_at"] = closed_at
        if created_at is not None:
            updates["created_at"] = created_at
        if updated_at is not None:
            updates["updated_at"] = updated_at
        await tickets_repo.update_ticket(int(existing["id"]), **updates)
        return "updated"

    created = await tickets_repo.create_ticket(
        subject=subject,
        description=description_value,
        requester_id=None,
        company_id=company_id,
        assigned_user_id=None,
        priority=priority,
        status=status,
        category=category_value,
        module_slug=None,
        external_reference=external_reference,
    )
    created_id = created.get("id")
    if created_id is not None and any((created_at, updated_at, closed_at)):
        timestamp_updates: dict[str, Any] = {}
        if created_at is not None:
            timestamp_updates["created_at"] = created_at
        if updated_at is not None:
            timestamp_updates["updated_at"] = updated_at
        if closed_at is not None:
            timestamp_updates["closed_at"] = closed_at
        await tickets_repo.update_ticket(int(created_id), **timestamp_updates)
    return "created"


async def import_ticket_by_id(
    ticket_id: int,
    *,
    rate_limiter: syncro.AsyncRateLimiter | None = None,
) -> TicketImportSummary:
    limiter = rate_limiter or _DEFAULT_RATE_LIMITER
    summary = TicketImportSummary(mode="single")
    log_info("Starting Syncro ticket import", mode="single", ticket_id=ticket_id)
    ticket = await syncro.get_ticket(ticket_id, rate_limiter=limiter)
    if not ticket:
        summary.skipped += 1
        log_info("Syncro ticket import completed", mode="single", fetched=summary.fetched, created=0, updated=0, skipped=1)
        return summary
    summary.fetched = 1
    outcome = await _upsert_ticket(ticket)
    summary.record(outcome)
    log_info(
        "Syncro ticket import completed",
        mode="single",
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        skipped=summary.skipped,
    )
    return summary


async def import_ticket_range(
    start_id: int,
    end_id: int,
    *,
    rate_limiter: syncro.AsyncRateLimiter | None = None,
) -> TicketImportSummary:
    limiter = rate_limiter or _DEFAULT_RATE_LIMITER
    summary = TicketImportSummary(mode="range")
    log_info("Starting Syncro ticket import", mode="range", start_id=start_id, end_id=end_id)
    for identifier in range(start_id, end_id + 1):
        ticket = await syncro.get_ticket(identifier, rate_limiter=limiter)
        if not ticket:
            summary.skipped += 1
            continue
        summary.fetched += 1
        outcome = await _upsert_ticket(ticket)
        summary.record(outcome)
    log_info(
        "Syncro ticket import completed",
        mode="range",
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        skipped=summary.skipped,
    )
    return summary


def _extract_total_pages(meta: dict[str, Any]) -> int | None:
    candidates = [meta.get("total_pages"), meta.get("totalPages"), meta.get("total")]
    for candidate in candidates:
        try:
            if candidate is None:
                continue
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


async def import_all_tickets(
    *,
    rate_limiter: syncro.AsyncRateLimiter | None = None,
) -> TicketImportSummary:
    limiter = rate_limiter or _DEFAULT_RATE_LIMITER
    summary = TicketImportSummary(mode="all")
    log_info("Starting Syncro ticket import", mode="all")
    page = 1
    total_pages: int | None = None
    while True:
        tickets, meta = await syncro.list_tickets(page=page, rate_limiter=limiter)
        if not tickets:
            break
        summary.fetched += len(tickets)
        for ticket in tickets:
            try:
                outcome = await _upsert_ticket(ticket)
            except Exception as exc:  # pragma: no cover - defensive logging
                log_error("Failed to import Syncro ticket", syncro_id=ticket.get("id"), error=str(exc))
                summary.skipped += 1
                continue
            summary.record(outcome)
        if total_pages is None:
            total_pages = _extract_total_pages(meta)
        if total_pages is not None and page >= total_pages:
            break
        page += 1
    log_info(
        "Syncro ticket import completed",
        mode="all",
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        skipped=summary.skipped,
    )
    return summary


async def import_from_request(
    *,
    mode: str,
    ticket_id: int | None = None,
    start_id: int | None = None,
    end_id: int | None = None,
    rate_limiter: syncro.AsyncRateLimiter | None = None,
) -> TicketImportSummary:
    mode_lower = mode.lower()
    if mode_lower == "single":
        if ticket_id is None:
            raise ValueError("ticket_id is required for single imports")
        return await import_ticket_by_id(ticket_id, rate_limiter=rate_limiter)
    if mode_lower == "range":
        if start_id is None or end_id is None:
            raise ValueError("start_id and end_id are required for range imports")
        if end_id < start_id:
            raise ValueError("end_id must be greater than or equal to start_id")
        return await import_ticket_range(start_id, end_id, rate_limiter=rate_limiter)
    if mode_lower == "all":
        return await import_all_tickets(rate_limiter=rate_limiter)
    raise ValueError("mode must be one of 'single', 'range', or 'all'")
