from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import re
import json
from typing import Any

from app.core.logging import log_error, log_info
from app.repositories import companies as company_repo
from app.repositories import tickets as tickets_repo
from app.repositories import users as user_repo
from app.repositories import webhook_events as webhook_events_repo
from app.services import syncro, webhook_monitor

_ALLOWED_PRIORITIES = {"urgent", "high", "normal", "low"}
_ALLOWED_STATUSES = {"open", "in_progress", "pending", "resolved", "closed"}
_DEFAULT_PRIORITY = "normal"
_DEFAULT_STATUS = "open"

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
_HTML_NEWLINE_TAGS = re.compile(
    r"<\s*(?:br\s*/?|/(?:p|div|li|tr|table|thead|tbody|tfoot|section|article|header|footer|h[1-6]))\b[^>]*>",
    flags=re.IGNORECASE,
)
_HTML_TAGS = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    normalised = unescape(text.replace("\r\n", "\n")).replace("\xa0", " ")
    normalised = _HTML_NEWLINE_TAGS.sub("\n", normalised)
    normalised = _HTML_TAGS.sub("", normalised)
    normalised = re.sub(r"[\t ]*\n[\t ]*", "\n", normalised)
    normalised = re.sub(r"\n{2,}", "\n", normalised)
    normalised = normalised.strip()
    return normalised or None


def _normalise_status(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "status", "label"):
            text_value = value.get(key)
            if text_value:
                value = text_value
                break
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
    if isinstance(value, dict):
        for key in ("name", "priority", "label"):
            text_value = value.get(key)
            if text_value:
                value = text_value
                break
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


def _extract_comment_body(comment: dict[str, Any]) -> str | None:
    return _clean_text(
        comment.get("body")
        or comment.get("comment")
        or comment.get("text")
        or comment.get("content")
    )


def _extract_comment_subject(comment: dict[str, Any]) -> str | None:
    for key in ("subject", "title", "summary"):
        candidate = _clean_text(comment.get(key))
        if candidate:
            return candidate
    return None


def _extract_description(ticket: dict[str, Any]) -> str | None:
    for key in ("problem", "description", "issue", "body", "notes"):
        candidate = _clean_text(ticket.get(key))
        if candidate:
            return candidate
    for comment in _extract_comments(ticket):
        subject = _extract_comment_subject(comment)
        if subject and subject.lower() == "initial issue":
            body = _extract_comment_body(comment)
            if body:
                return body
    return None


def _extract_ticket_number(ticket: dict[str, Any]) -> str | None:
    candidates = [
        ticket.get("ticket_number"),
        ticket.get("ticketNumber"),
        ticket.get("number"),
        ticket.get("ticket_no"),
        ticket.get("ticketNo"),
    ]
    for candidate in candidates:
        text = _clean_text(candidate)
        if text:
            return text
    fallback = ticket.get("id")
    return str(fallback) if fallback is not None else None


def _iter_company_name_candidates(ticket: dict[str, Any]):
    fields = [
        ticket.get("customer_business_then_name"),
        ticket.get("business_then_name"),
        ticket.get("customer_business_name"),
        ticket.get("customer_name"),
    ]
    customer = ticket.get("customer")
    if isinstance(customer, dict):
        fields.extend(
            [
                customer.get("business_then_name"),
                customer.get("business_name"),
                customer.get("name"),
                customer.get("company_name"),
            ]
        )
    for field in fields:
        text = _clean_text(field)
        if not text:
            continue
        yield text
        segments = [segment.strip() for segment in re.split(r"\s*[-–—]\s*", text) if segment.strip()]
        if segments:
            yield segments[0]


def _extract_syncro_company_ids(ticket: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    syncro_ids: list[str] = []
    keys = ("customer_id", "customerId", "customerid", "client_id")
    for key in keys:
        value = ticket.get(key)
        text = _clean_text(value)
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            syncro_ids.append(text)
    customer = ticket.get("customer")
    if isinstance(customer, dict):
        for key in ("id", "customer_id"):
            value = customer.get(key)
            text = _clean_text(value)
            if not text:
                continue
            if text not in seen:
                seen.add(text)
                syncro_ids.append(text)
    return syncro_ids


def _normalise_email(value: Any) -> str | None:
    text = _clean_text(value)
    if not text or "@" not in text:
        return None
    return text


def _extract_contact_email(ticket: dict[str, Any]) -> str | None:
    contact = ticket.get("contact")
    if isinstance(contact, dict):
        for key in ("email", "primary_email", "contact_email"):
            email = _normalise_email(contact.get(key))
            if email:
                return email
    for key in ("contact_email", "contactEmail", "customer_email", "email"):
        email = _normalise_email(ticket.get(key))
        if email:
            return email
    return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = _clean_text(value)
    if not text:
        return False
    return text.lower() in {"1", "true", "yes", "y", "t"}


def _extract_comments(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("comments", "ticket_comments", "ticketComments"):
        comments = ticket.get(key)
        if isinstance(comments, list):
            return [comment for comment in comments if isinstance(comment, dict)]
    return []


def _extract_destination_emails(comment: dict[str, Any]) -> set[str]:
    raw = comment.get("destination_emails") or comment.get("destinationEmails")
    emails: set[str] = set()

    def _add(candidate: Any) -> None:
        email = _normalise_email(candidate)
        if email:
            emails.add(email)

    if isinstance(raw, str):
        for segment in re.split(r"[,;\s]+", raw):
            _add(segment)
    elif isinstance(raw, dict):
        for key in ("email", "address", "value"):
            _add(raw.get(key))
    elif isinstance(raw, (list, tuple, set)):
        for item in raw:
            if isinstance(item, dict):
                for key in ("email", "address", "value"):
                    _add(item.get(key))
            else:
                _add(item)
    return emails


def _gather_comment_watchers(comments: list[dict[str, Any]]) -> set[str]:
    watchers: dict[str, str] = {}
    for comment in comments:
        for email in _extract_destination_emails(comment):
            key = email.lower()
            if key not in watchers:
                watchers[key] = email
    return set(watchers.values())


def _should_comment_be_internal(comment: dict[str, Any]) -> bool:
    tech = _clean_text(comment.get("tech"))
    if tech and tech.lower() == "customer-reply":
        return False
    return _coerce_bool(comment.get("hidden"))


async def _resolve_user_id_by_email(email: str | None) -> int | None:
    if not email:
        return None
    try:
        user = await user_repo.get_user_by_email(email)
    except RuntimeError as exc:  # pragma: no cover - defensive logging
        log_error("Failed to resolve user from email", email=email, error=str(exc))
        return None
    if not user or user.get("id") is None:
        return None
    try:
        return int(user["id"])
    except (TypeError, ValueError):
        return None


def _extract_comment_author_email(comment: dict[str, Any]) -> str | None:
    for key in (
        "user_email",
        "userEmail",
        "author_email",
        "authorEmail",
        "from_email",
        "fromEmail",
        "email",
        "sender",
        "sender_email",
        "senderEmail",
        "reply_to",
        "replyTo",
        "tech_email",
        "techEmail",
    ):
        email = _normalise_email(comment.get(key))
        if email:
            return email
    for key in ("user", "author", "created_by", "creator"):
        nested = comment.get(key)
        if isinstance(nested, dict):
            for nested_key in ("email", "user_email", "address", "value"):
                email = _normalise_email(nested.get(nested_key))
                if email:
                    return email
    return None


async def _resolve_comment_author_id(
    comment: dict[str, Any],
    *,
    requester_id: int | None,
    contact_email: str | None,
    cache: dict[str, int | None],
) -> int | None:
    tech = _clean_text(comment.get("tech"))
    if tech and tech.lower() == "customer-reply":
        if requester_id is not None:
            return requester_id
        if contact_email:
            key = contact_email.lower()
            if key not in cache:
                cache[key] = await _resolve_user_id_by_email(contact_email)
            return cache[key]
        return None
    email = _extract_comment_author_email(comment)
    if not email:
        return None
    key = email.lower()
    if key not in cache:
        cache[key] = await _resolve_user_id_by_email(email)
    return cache[key]


async def _sync_ticket_replies(
    ticket_id: int,
    comments: list[dict[str, Any]],
    *,
    requester_id: int | None,
    contact_email: str | None,
) -> None:
    if not comments:
        return
    try:
        existing = await tickets_repo.list_replies(ticket_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to fetch existing ticket replies", ticket_id=ticket_id, error=str(exc))
        existing = []
    known_refs = {
        str(reply.get("external_reference"))
        for reply in existing
        if reply.get("external_reference") is not None
    }
    author_cache: dict[str, int | None] = {}
    for comment in comments:
        body = _extract_comment_body(comment)
        if not body:
            continue
        external_ref_raw = (
            comment.get("id")
            or comment.get("comment_id")
            or comment.get("commentId")
            or comment.get("guid")
        )
        external_ref = str(external_ref_raw) if external_ref_raw is not None else None
        if external_ref and external_ref in known_refs:
            continue
        created_at = _parse_datetime(
            comment.get("created_at")
            or comment.get("created_on")
            or comment.get("created")
            or comment.get("updated_at")
        )
        is_internal = _should_comment_be_internal(comment)
        author_id = await _resolve_comment_author_id(
            comment,
            requester_id=requester_id,
            contact_email=contact_email,
            cache=author_cache,
        )
        await tickets_repo.create_reply(
            ticket_id=ticket_id,
            author_id=author_id,
            body=body,
            is_internal=is_internal,
            external_reference=external_ref,
            created_at=created_at,
        )
        if external_ref:
            known_refs.add(external_ref)


async def _sync_ticket_watchers(
    ticket_id: int,
    comments: list[dict[str, Any]],
    contact_email: str | None,
) -> None:
    watchers = _gather_comment_watchers(comments)
    if contact_email:
        watchers = {email for email in watchers if email.lower() != contact_email.lower()}
    watchers = {
        email
        for email in watchers
        if email.lower() != "support@hawkinsitsolutions.com.au"
    }
    if not watchers:
        return
    try:
        existing = await tickets_repo.list_watchers(ticket_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to fetch existing ticket watchers", ticket_id=ticket_id, error=str(exc))
        existing = []
    existing_ids = {
        int(watcher["user_id"])
        for watcher in existing
        if watcher.get("user_id") is not None
    }
    for email in sorted(watchers, key=lambda value: value.lower()):
        user_id = await _resolve_user_id_by_email(email)
        if user_id is None or user_id in existing_ids:
            continue
        try:
            await tickets_repo.add_watcher(ticket_id, user_id)
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to add ticket watcher",
                ticket_id=ticket_id,
                email=email,
                error=str(exc),
            )
            continue
        existing_ids.add(user_id)


async def _resolve_company_id(ticket: dict[str, Any]) -> int | None:
    syncro_ids = _extract_syncro_company_ids(ticket)
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
    name_candidates = list(_iter_company_name_candidates(ticket))
    for name in name_candidates:
        try:
            company = await company_repo.get_company_by_name(name)
        except RuntimeError as exc:  # pragma: no cover - defensive logging
            log_error("Failed to resolve company from name", company_name=name, error=str(exc))
            continue
        if company and company.get("id") is not None:
            try:
                return int(company["id"])
            except (TypeError, ValueError):
                continue
    primary_name = name_candidates[0] if name_candidates else None
    primary_syncro_id = syncro_ids[0] if syncro_ids else None
    if not primary_name and primary_syncro_id:
        primary_name = f"Syncro Customer {primary_syncro_id}"
    if not primary_name:
        return None
    payload: dict[str, Any] = {"name": primary_name}
    if primary_syncro_id:
        payload["syncro_company_id"] = primary_syncro_id
    try:
        created = await company_repo.create_company(**payload)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to auto-create company from Syncro ticket",
            company_name=primary_name,
            syncro_company_id=primary_syncro_id,
            error=str(exc),
        )
        return None
    created_id = created.get("id") if isinstance(created, dict) else None
    if created_id is None:
        return None
    log_info(
        "Auto-created company from Syncro ticket",
        company_id=created_id,
        company_name=primary_name,
        syncro_company_id=primary_syncro_id,
    )
    try:
        return int(created_id)
    except (TypeError, ValueError):
        return None
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
    ticket_number = _extract_ticket_number(ticket)
    contact_email = _extract_contact_email(ticket)
    requester_id = await _resolve_user_id_by_email(contact_email)

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
            "ticket_number": ticket_number,
            "requester_id": requester_id,
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
        ticket_db_id = int(existing["id"])
        outcome = "updated"
    else:
        created = await tickets_repo.create_ticket(
            subject=subject,
            description=description_value,
            requester_id=requester_id,
            company_id=company_id,
            assigned_user_id=None,
            priority=priority,
            status=status,
            category=category_value,
            module_slug=None,
            external_reference=external_reference,
            ticket_number=ticket_number,
        )
        created_id = created.get("id")
        ticket_db_id = int(created_id) if created_id is not None else None
        if created_id is not None and any((created_at, updated_at, closed_at)):
            timestamp_updates: dict[str, Any] = {}
            if created_at is not None:
                timestamp_updates["created_at"] = created_at
            if updated_at is not None:
                timestamp_updates["updated_at"] = updated_at
            if closed_at is not None:
                timestamp_updates["closed_at"] = closed_at
            await tickets_repo.update_ticket(int(created_id), **timestamp_updates)
        outcome = "created"

    comments = _extract_comments(ticket)
    if ticket_db_id is not None:
        await _sync_ticket_replies(
            ticket_db_id,
            comments,
            requester_id=requester_id,
            contact_email=contact_email,
        )
        await _sync_ticket_watchers(ticket_db_id, comments, contact_email)

    return outcome


async def import_ticket_by_id(
    ticket_id: int,
    *,
    rate_limiter: syncro.AsyncRateLimiter | None = None,
) -> TicketImportSummary:
    limiter = rate_limiter or await syncro.get_rate_limiter()
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
    limiter = rate_limiter or await syncro.get_rate_limiter()
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
    limiter = rate_limiter or await syncro.get_rate_limiter()
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


def _build_import_target(
    mode: str, ticket_id: int | None, start_id: int | None, end_id: int | None
) -> str:
    base = f"syncro://tickets/import?mode={mode}"
    if mode == "single" and ticket_id is not None:
        return f"{base}&ticketId={ticket_id}"
    if mode == "range":
        params: list[str] = []
        if start_id is not None:
            params.append(f"startId={start_id}")
        if end_id is not None:
            params.append(f"endId={end_id}")
        if params:
            return f"{base}&{'&'.join(params)}"
    return base


async def import_from_request(
    *,
    mode: str,
    ticket_id: int | None = None,
    start_id: int | None = None,
    end_id: int | None = None,
    rate_limiter: syncro.AsyncRateLimiter | None = None,
) -> TicketImportSummary:
    mode_lower = mode.lower()
    payload: dict[str, Any] = {"mode": mode_lower}
    if ticket_id is not None:
        payload["ticketId"] = ticket_id
    if start_id is not None:
        payload["startId"] = start_id
    if end_id is not None:
        payload["endId"] = end_id

    event_id: int | None = None
    using_monitor: bool = False
    target_url = _build_import_target(mode_lower, ticket_id, start_id, end_id)
    log_info(
        "Initialising Syncro ticket import workflow",
        mode=mode_lower,
        target_url=target_url,
        payload_keys=sorted(payload.keys()),
        has_rate_limiter=bool(rate_limiter),
    )

    def _coerce_event_id(raw_id: Any) -> int | None:
        try:
            return int(raw_id)
        except (TypeError, ValueError):  # pragma: no cover - defensive casting
            return None

    try:
        event = await webhook_monitor.create_manual_event(
            name="syncro.ticket.import",
            target_url=target_url,
            payload=payload,
            max_attempts=1,
            backoff_seconds=0,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to record Syncro ticket import in webhook monitor",
            mode=mode_lower,
            error=str(exc),
        )
        event = None
    else:
        event_id = _coerce_event_id(event.get("id")) if event else None
        using_monitor = event_id is not None
        log_info(
            "Syncro ticket import monitor event recorded",
            mode=mode_lower,
            event_id=event_id,
            using_monitor=using_monitor,
        )

    if event_id is None:
        try:
            fallback_event = await webhook_events_repo.create_event(
                name="syncro.ticket.import",
                target_url=target_url,
                payload=payload,
                max_attempts=1,
                backoff_seconds=0,
            )
        except Exception as fallback_exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to create fallback Syncro ticket import event",
                mode=mode_lower,
                error=str(fallback_exc),
            )
            fallback_event = None
        else:
            fallback_raw_id = fallback_event.get("id") if fallback_event else None
            event_id = _coerce_event_id(fallback_raw_id)
            if event_id is not None:
                try:
                    await webhook_events_repo.mark_in_progress(event_id)
                except Exception as mark_exc:  # pragma: no cover - defensive logging
                    log_error(
                        "Failed to mark fallback Syncro ticket import event in progress",
                        event_id=event_id,
                        error=str(mark_exc),
                    )
                    event_id = None
                else:
                    using_monitor = False
                    log_info(
                        "Syncro ticket import fallback event initialised",
                        mode=mode_lower,
                        event_id=event_id,
                    )
            else:
                log_error(
                    "Syncro ticket import fallback event returned without identifier",
                    mode=mode_lower,
                )
    else:
        log_info(
            "Syncro ticket import monitor event will track execution",
            mode=mode_lower,
            event_id=event_id,
        )

    attempt_number = 1

    async def _record_failure(error: Exception) -> None:
        if event_id is None:
            log_error(
                "Syncro ticket import failure without event tracking",
                mode=mode_lower,
                error=str(error),
            )
            return
        try:
            if using_monitor:
                await webhook_monitor.record_manual_failure(
                    event_id,
                    attempt_number=attempt_number,
                    status="failed",
                    error_message=str(error),
                    response_status=None,
                    response_body=None,
                )
            else:
                await webhook_events_repo.record_attempt(
                    event_id=event_id,
                    attempt_number=attempt_number,
                    status="failed",
                    response_status=None,
                    response_body=None,
                    error_message=str(error),
                )
                await webhook_events_repo.mark_event_failed(
                    event_id,
                    attempt_number=attempt_number,
                    error_message=str(error),
                    response_status=None,
                    response_body=None,
                )
        except Exception as record_exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to record Syncro ticket import failure",
                event_id=event_id,
                error=str(record_exc),
            )
        else:
            log_error(
                "Syncro ticket import execution failed",
                mode=mode_lower,
                event_id=event_id,
                error=str(error),
                attempt=attempt_number,
            )

    try:
        if mode_lower == "single":
            if ticket_id is None:
                raise ValueError("ticket_id is required for single imports")
            summary = await import_ticket_by_id(ticket_id, rate_limiter=rate_limiter)
        elif mode_lower == "range":
            if start_id is None or end_id is None:
                raise ValueError("start_id and end_id are required for range imports")
            if end_id < start_id:
                raise ValueError("end_id must be greater than or equal to start_id")
            summary = await import_ticket_range(start_id, end_id, rate_limiter=rate_limiter)
        elif mode_lower == "all":
            summary = await import_all_tickets(rate_limiter=rate_limiter)
        else:
            raise ValueError("mode must be one of 'single', 'range', or 'all'")
    except Exception as exc:
        await _record_failure(exc)
        raise

    if event_id is not None:
        response_body = json.dumps(summary.as_dict())
        try:
            if using_monitor:
                await webhook_monitor.record_manual_success(
                    event_id,
                    attempt_number=attempt_number,
                    response_status=200,
                    response_body=response_body,
                )
            else:
                await webhook_events_repo.record_attempt(
                    event_id=event_id,
                    attempt_number=attempt_number,
                    status="succeeded",
                    response_status=200,
                    response_body=response_body,
                    error_message=None,
                )
                await webhook_events_repo.mark_event_completed(
                    event_id,
                    attempt_number=attempt_number,
                    response_status=200,
                    response_body=response_body,
                )
        except Exception as record_exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to record Syncro ticket import success",
                event_id=event_id,
                error=str(record_exc),
            )
        else:
            log_info(
                "Syncro ticket import execution recorded",
                mode=mode_lower,
                event_id=event_id,
                attempt=attempt_number,
                using_monitor=using_monitor,
            )
    return summary
