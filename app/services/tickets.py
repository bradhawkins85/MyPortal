from __future__ import annotations

import html
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping as MappingABC, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.database import db
from app.core.logging import log_error
from app.repositories import tickets as tickets_repo
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories.tickets import TicketRecord
from app.services import automations as automations_service
from app.repositories import users as user_repo
from app.services import modules as modules_service
from app.services.tagging import filter_helpful_slugs, is_helpful_slug, slugify_tag
from app.services.sanitization import sanitize_rich_text
from app.services.realtime import RefreshNotifier, refresh_notifier

HELPDESK_PERMISSION_KEY = "helpdesk.technician"

_REPLY_ABOVE_PATTERN = re.compile(
    r"^-{3,}\s*reply\s+above\s+this\s+line\s+to\s+add\s+a\s+comment\s*-{0,}\s*$",
    re.IGNORECASE,
)

_SIGNATURE_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^--+\s*$"),
    re.compile(r"^__+\s*$"),
    re.compile(r"^cheers[\W_]*$", re.IGNORECASE),
    re.compile(r"^thanks[\W_]*$", re.IGNORECASE),
    re.compile(r"^thank\s+you[\W_]*$", re.IGNORECASE),
    re.compile(r"^regards[\W_]*$", re.IGNORECASE),
    re.compile(r"^kind\s+regards[\W_]*$", re.IGNORECASE),
    re.compile(r"^best[\W_]*$", re.IGNORECASE),
    re.compile(r"^best\s+regards[\W_]*$", re.IGNORECASE),
    re.compile(r"^sincerely[\W_]*$", re.IGNORECASE),
    re.compile(r"^sent\s+from\s+my\s+.+$", re.IGNORECASE),
)

_SIGNATURE_KEYWORDS: tuple[str, ...] = (
    "kind regards",
    "warm regards",
    "with thanks",
    "many thanks",
    "yours faithfully",
    "yours sincerely",
)

_DISCLAIMER_KEYWORDS: tuple[str, ...] = (
    "confidential",
    "disclaimer",
    "privileged",
    "intended recipient",
    "unauthorized",
    "unauthorised",
    "may contain information",
)

_PROMPT_HEADER = (
    "You are an AI assistant that summarises helpdesk tickets for technicians. "
    "Return a compact JSON object with the keys 'summary' and 'resolution'. "
    "The 'resolution' value must be either 'Likely Resolved' or 'Likely In Progress'. "
    "Only choose 'Likely Resolved' when the conversation clearly shows the issue has been addressed or the requester confirmed resolution. "
    "If there is not enough evidence of resolution, respond with 'Likely In Progress'."
)

_TAGS_PROMPT_HEADER = (
    "You are an AI assistant that analyses helpdesk tickets and suggests between five and ten short tags. "
    "Tags must describe the issues, affected systems, troubleshooting steps, impacted users, and requested actions. "
    "Respond ONLY with JSON shaped as {\"tags\": [\"tag-one\", \"tag-two\", ...]} using lowercase kebab-case tags."
)

_DEFAULT_TAG_FILL = [
    "support-request",
    "needs-triage",
    "customer-impact",
    "technical-issue",
    "follow-up-needed",
    "service-disruption",
    "awaiting-update",
    "priority-review",
    "diagnostics",
    "knowledge-base",
]


_MAX_TICKET_DESCRIPTION_BYTES = 65_535
_TRUNCATION_NOTICE = "\n\n[Message truncated due to size]"


def _truncate_description(description: str | None) -> str | None:
    if description is None:
        return None
    text = str(description)
    if not text:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= _MAX_TICKET_DESCRIPTION_BYTES:
        return text
    allowance = max(_MAX_TICKET_DESCRIPTION_BYTES - len(_TRUNCATION_NOTICE.encode("utf-8")), 0)
    truncated_bytes = encoded[:allowance]
    truncated_text = truncated_bytes.decode("utf-8", errors="ignore").rstrip()
    if truncated_text:
        return f"{truncated_text}{_TRUNCATION_NOTICE}"
    return _TRUNCATION_NOTICE.lstrip()


async def update_ticket_description(
    ticket_id: int, description: str | None
) -> TicketRecord | None:
    """Persist a ticket description after enforcing size limits."""

    record = await tickets_repo.update_ticket(
        ticket_id,
        description=_truncate_description(description),
    )
    await broadcast_ticket_event(action="updated", ticket_id=ticket_id)
    return record


async def _safely_call(async_fn: Callable[..., Awaitable[Any]], *args, **kwargs):
    try:
        return await async_fn(*args, **kwargs)
    except RuntimeError as error:
        message = str(error)
        if "Database pool not initialised" in message or not db.is_connected():
            return None
        raise


def _format_user_display_name(user: Mapping[str, Any] | None) -> str | None:
    if not isinstance(user, Mapping):
        return None
    first = str(user.get("first_name") or "").strip()
    last = str(user.get("last_name") or "").strip()
    if first and last:
        return f"{first} {last}".strip()
    if first or last:
        return (first or last).strip()
    email = str(user.get("email") or "").strip()
    return email or None


async def _resolve_user_snapshot(
    user_value: Mapping[str, Any] | None,
    user_id: Any,
) -> Mapping[str, Any] | None:
    if isinstance(user_value, Mapping):
        return dict(user_value)
    try:
        numeric_id = int(user_id)
    except (TypeError, ValueError):
        return None
    fetched = await _safely_call(user_repo.get_user_by_id, numeric_id)
    if isinstance(fetched, Mapping):
        return dict(fetched)
    return None


def _build_user_snapshot(user: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(user, Mapping):
        return None
    snapshot = {
        "id": user.get("id"),
        "email": user.get("email"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "mobile_phone": user.get("mobile_phone"),
        "company_id": user.get("company_id"),
        "is_super_admin": user.get("is_super_admin"),
    }
    display_name = _format_user_display_name(user)
    if display_name:
        snapshot["display_name"] = display_name
    return snapshot


async def _enrich_ticket_context(ticket: Mapping[str, Any]) -> TicketRecord:
    enriched: TicketRecord = dict(ticket)

    company_value = enriched.get("company") if isinstance(enriched.get("company"), Mapping) else None
    company_id = enriched.get("company_id")
    if not isinstance(company_value, Mapping) and isinstance(company_id, int):
        company_value = await _safely_call(company_repo.get_company_by_id, company_id)
    if isinstance(company_value, Mapping):
        enriched["company"] = dict(company_value)
        enriched["company_name"] = company_value.get("name")
    else:
        enriched.setdefault("company", None)
        enriched.setdefault("company_name", None)

    assigned_value = enriched.get("assigned_user") if isinstance(enriched.get("assigned_user"), Mapping) else None
    assigned_user_id = enriched.get("assigned_user_id")
    assigned_user = await _resolve_user_snapshot(assigned_value, assigned_user_id)
    assigned_snapshot = _build_user_snapshot(assigned_user)
    if assigned_snapshot:
        enriched["assigned_user"] = assigned_snapshot
        enriched["assigned_user_email"] = assigned_snapshot.get("email")
        enriched["assigned_user_display_name"] = assigned_snapshot.get("display_name")
    else:
        enriched.setdefault("assigned_user", None)
        enriched["assigned_user_email"] = None
        enriched["assigned_user_display_name"] = None

    requester_value = enriched.get("requester") if isinstance(enriched.get("requester"), Mapping) else None
    requester_id = enriched.get("requester_id")
    requester_user = await _resolve_user_snapshot(requester_value, requester_id)
    requester_snapshot = _build_user_snapshot(requester_user)
    if requester_snapshot:
        enriched["requester"] = requester_snapshot
        enriched["requester_email"] = requester_snapshot.get("email")
        enriched["requester_display_name"] = requester_snapshot.get("display_name")
    else:
        enriched.setdefault("requester", None)
        enriched["requester_email"] = None
        enriched["requester_display_name"] = None

    ticket_id = enriched.get("id")
    watchers: list[Mapping[str, Any]] = []
    if isinstance(ticket_id, int):
        fetched_watchers = await _safely_call(tickets_repo.list_watchers, ticket_id)
        if isinstance(fetched_watchers, list):
            watchers = [record for record in fetched_watchers if isinstance(record, Mapping)]
    elif isinstance(enriched.get("watchers"), list):
        watchers = [record for record in enriched.get("watchers") if isinstance(record, Mapping)]

    watcher_entries: list[dict[str, Any]] = []
    watcher_emails: list[str] = []
    for watcher in watchers:
        user_value = watcher.get("user") if isinstance(watcher.get("user"), Mapping) else None
        user_id = watcher.get("user_id")
        resolved_user = await _resolve_user_snapshot(user_value, user_id)
        snapshot = _build_user_snapshot(resolved_user)
        entry: dict[str, Any] = {
            "id": watcher.get("id"),
            "ticket_id": watcher.get("ticket_id"),
            "user_id": watcher.get("user_id"),
            "created_at": watcher.get("created_at"),
            "user": snapshot,
            "email": snapshot.get("email") if snapshot else None,
            "display_name": snapshot.get("display_name") if snapshot else None,
        }
        if entry["email"]:
            watcher_emails.append(entry["email"])
        watcher_entries.append(entry)
    enriched["watchers"] = watcher_entries
    enriched["watchers_count"] = len(watcher_entries)
    enriched["watcher_emails"] = watcher_emails

    replies: list[Mapping[str, Any]] = []
    if isinstance(ticket_id, int):
        fetched_replies = await _safely_call(
            tickets_repo.list_replies, ticket_id, include_internal=True
        )
        if isinstance(fetched_replies, list):
            replies = [record for record in fetched_replies if isinstance(record, Mapping)]

    latest_reply: dict[str, Any] | None = None
    if replies:
        reply = dict(replies[-1])
        author_value = reply.get("author") if isinstance(reply.get("author"), Mapping) else None
        author_id = reply.get("author_id")
        author_user = await _resolve_user_snapshot(author_value, author_id)
        author_snapshot = _build_user_snapshot(author_user)
        reply["author"] = author_snapshot
        reply["author_email"] = author_snapshot.get("email") if author_snapshot else None
        reply["author_display_name"] = (
            author_snapshot.get("display_name") if author_snapshot else None
        )
        latest_reply = reply
    enriched["latest_reply"] = latest_reply

    return enriched


def _normalise_reply_marker_line(value: str) -> str:
    candidate = html.unescape(value).strip()
    candidate = candidate.replace("\u200b", "").replace("\ufeff", "").replace("\xad", "")
    while candidate.startswith(">"):
        candidate = candidate[1:].lstrip()
    while candidate.endswith(">"):
        candidate = candidate[:-1].rstrip()
    return candidate


def _strip_reply_marker(text: str) -> str:
    if not text:
        return ""

    lines = text.splitlines()
    cutoff = len(lines)
    for index, raw_line in enumerate(lines):
        candidate = _normalise_reply_marker_line(raw_line)
        if _REPLY_ABOVE_PATTERN.match(candidate):
            cutoff = index
            break

    trimmed = lines[:cutoff]
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()

    return "\n".join(trimmed).strip()


def _strip_signature_block(text: str) -> str:
    if not text:
        return ""

    lines = text.splitlines()
    cutoff = len(lines)

    def _looks_like_signature(candidate: str) -> bool:
        if not candidate:
            return False
        lower = candidate.lower()
        if any(pattern.match(candidate) for pattern in _SIGNATURE_LINE_PATTERNS):
            return True
        if any(lower.startswith(prefix) for prefix in _SIGNATURE_KEYWORDS):
            return True
        if lower.endswith(",") and _looks_like_signature(lower[:-1].strip()):
            return True
        if lower.startswith("sent from my"):
            return True
        if any(keyword in lower for keyword in _DISCLAIMER_KEYWORDS):
            return True
        return False

    for index in range(len(lines) - 1, -1, -1):
        candidate = lines[index].strip()
        if not candidate:
            continue
        distance = len(lines) - index
        if _looks_like_signature(candidate):
            cutoff = index
            continue
        if distance > 12:
            break

    trimmed = lines[:cutoff]
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return "\n".join(trimmed).strip()


_HEADER_PREFIXES = (
    "subject:",
    "from:",
    "reply-to:",
    "date:",
)


def _strip_conversation_noise(text: str) -> str:
    if not text:
        return ""
    cleaned = _strip_reply_marker(text)
    cleaned = _strip_signature_block(cleaned)

    lines = cleaned.splitlines()
    filtered_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            filtered_lines.append(line)
            continue
        lower = stripped.lower()
        if any(lower.startswith(prefix) for prefix in _HEADER_PREFIXES):
            continue
        filtered_lines.append(line)

    return "\n".join(filtered_lines).strip()


def _prepare_prompt_text(value: Any) -> str:
    sanitized = sanitize_rich_text(str(value or ""))
    html_value = sanitized.html or ""
    with_breaks = re.sub(r"<br\s*/?>", "\n", html_value, flags=re.IGNORECASE)
    block_breaks = re.sub(
        r"</(p|div|li|tr|td|th|tbody|thead|ul|ol)>",
        "\n",
        with_breaks,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", block_breaks)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [line.strip() for line in text.splitlines()]
    normalised = "\n".join(line for line in lines if line)
    return _strip_conversation_noise(normalised)


async def refresh_ticket_ai_summary(ticket_id: int) -> None:
    """Refresh the Ollama-generated summary for a ticket if the module is configured."""

    try:
        ticket = await tickets_repo.get_ticket(ticket_id)
    except RuntimeError as exc:  # pragma: no cover - defensive for missing database
        log_error("Ticket AI summary skipped", ticket_id=ticket_id, error=str(exc))
        return
    if not ticket:
        return

    replies = await _safely_call(tickets_repo.list_replies, ticket_id, include_internal=True) or []
    user_lookup: dict[int, Mapping[str, Any]] = {}
    user_ids: set[int] = set()

    for key in ("requester_id", "assigned_user_id"):
        value = ticket.get(key)
        if isinstance(value, int):
            user_ids.add(value)
    for reply in replies:
        author_id = reply.get("author_id")
        if isinstance(author_id, int):
            user_ids.add(author_id)

    for identifier in user_ids:
        record = await _safely_call(user_repo.get_user_by_id, identifier)
        if record:
            user_lookup[identifier] = record

    prompt = _render_prompt(ticket, replies, user_lookup)
    now = datetime.now(timezone.utc)

    await _safely_call(
        tickets_repo.update_ticket,
        ticket_id,
        ai_summary=None,
        ai_summary_status="queued",
        ai_summary_model=None,
        ai_resolution_state=None,
        ai_summary_updated_at=now,
    )

    async def _apply_result(result: Mapping[str, Any]) -> None:
        status_value = str(result.get("status") or result.get("event_status") or "unknown")
        model_value = result.get("model")
        payload = result.get("response")
        summary_text, resolution_label = _extract_summary_fields(payload)
        resolution_state = _normalise_resolution_state(resolution_label)
        if status_value == "succeeded" and summary_text and not resolution_state:
            resolution_state = "likely_in_progress"
        updated_at = datetime.now(timezone.utc)
        await _safely_call(
            tickets_repo.update_ticket,
            ticket_id,
            ai_summary=summary_text if status_value == "succeeded" else None,
            ai_summary_status=status_value,
            ai_summary_model=str(model_value) if isinstance(model_value, str) else None,
            ai_resolution_state=resolution_state if status_value == "succeeded" else None,
            ai_summary_updated_at=updated_at,
        )

    try:
        response = await modules_service.trigger_module(
            "ollama",
            {"prompt": prompt},
            on_complete=_apply_result,
        )
    except ValueError:
        await _safely_call(
            tickets_repo.update_ticket,
            ticket_id,
            ai_summary=None,
            ai_summary_status="skipped",
            ai_summary_model=None,
            ai_resolution_state=None,
            ai_summary_updated_at=now,
        )
        return
    except Exception as exc:  # pragma: no cover - network interaction
        log_error("Ticket AI summary failed", ticket_id=ticket_id, error=str(exc))
        await _safely_call(
            tickets_repo.update_ticket,
            ticket_id,
            ai_summary=None,
            ai_summary_status="error",
            ai_summary_model=None,
            ai_resolution_state=None,
            ai_summary_updated_at=now,
        )
        return

    status_value = str(response.get("status") or "unknown")
    if status_value == "skipped":
        await _safely_call(
            tickets_repo.update_ticket,
            ticket_id,
            ai_summary=None,
            ai_summary_status="skipped",
            ai_summary_model=None,
            ai_resolution_state=None,
            ai_summary_updated_at=now,
        )


async def refresh_ticket_ai_tags(ticket_id: int) -> None:
    """Refresh the Ollama-generated tags for a ticket if the module is configured."""

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        return

    replies = await tickets_repo.list_replies(ticket_id, include_internal=True)
    user_lookup: dict[int, Mapping[str, Any]] = {}
    user_ids: set[int] = set()

    for key in ("requester_id", "assigned_user_id"):
        value = ticket.get(key)
        if isinstance(value, int):
            user_ids.add(value)
    for reply in replies:
        author_id = reply.get("author_id")
        if isinstance(author_id, int):
            user_ids.add(author_id)

    for identifier in user_ids:
        record = await user_repo.get_user_by_id(identifier)
        if record:
            user_lookup[identifier] = record

    prompt = _render_tags_prompt(ticket, replies, user_lookup)
    now = datetime.now(timezone.utc)

    await _safely_call(
        tickets_repo.update_ticket,
        ticket_id,
        ai_tags=None,
        ai_tags_status="queued",
        ai_tags_model=None,
        ai_tags_updated_at=now,
    )

    async def _apply_result(result: Mapping[str, Any]) -> None:
        status_value = str(result.get("status") or result.get("event_status") or "unknown")
        model_value = result.get("model")
        payload = result.get("response")
        tags: list[str] | None = None
        if status_value == "succeeded":
            tags = _extract_tags(payload, ticket)
        updated_at = datetime.now(timezone.utc)
        await _safely_call(
            tickets_repo.update_ticket,
            ticket_id,
            ai_tags=tags if status_value == "succeeded" else None,
            ai_tags_status=status_value,
            ai_tags_model=str(model_value) if isinstance(model_value, str) else None,
            ai_tags_updated_at=updated_at,
        )

    try:
        response = await modules_service.trigger_module(
            "ollama",
            {"prompt": prompt},
            on_complete=_apply_result,
        )
    except ValueError:
        await _safely_call(
            tickets_repo.update_ticket,
            ticket_id,
            ai_tags=None,
            ai_tags_status="skipped",
            ai_tags_model=None,
            ai_tags_updated_at=now,
        )
        return
    except Exception as exc:  # pragma: no cover - network interaction
        log_error("Ticket AI tags failed", ticket_id=ticket_id, error=str(exc))
        await _safely_call(
            tickets_repo.update_ticket,
            ticket_id,
            ai_tags=None,
            ai_tags_status="error",
            ai_tags_model=None,
            ai_tags_updated_at=now,
        )
        return

    if str(response.get("status") or "").lower() == "skipped":
        await _safely_call(
            tickets_repo.update_ticket,
            ticket_id,
            ai_tags=None,
            ai_tags_status="skipped",
            ai_tags_model=None,
            ai_tags_updated_at=now,
        )


async def create_ticket(
    *,
    subject: str,
    description: str | None,
    requester_id: int | None,
    company_id: int | None,
    assigned_user_id: int | None,
    priority: str,
    status: str,
    category: str | None,
    module_slug: str | None,
    external_reference: str | None,
    ticket_number: str | None = None,
    trigger_automations: bool = True,
) -> TicketRecord:
    """Create a ticket and emit the corresponding automation event."""

    ticket = await tickets_repo.create_ticket(
        subject=subject,
        description=_truncate_description(description),
        requester_id=requester_id,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        priority=priority,
        status=status,
        category=category,
        module_slug=module_slug,
        external_reference=external_reference,
        ticket_number=ticket_number,
    )
    enriched_ticket = await _enrich_ticket_context(ticket)

    if trigger_automations:
        try:
            await automations_service.handle_event(
                "tickets.created",
                {"ticket": enriched_ticket},
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to execute ticket creation automations",
                ticket_id=ticket.get("id"),
                error=str(exc),
            )
    await broadcast_ticket_event(action="created", ticket_id=enriched_ticket.get("id"))
    return enriched_ticket


def _render_prompt(
    ticket: Mapping[str, Any],
    replies: list[Mapping[str, Any]],
    user_lookup: Mapping[int, Mapping[str, Any]],
) -> str:
    subject = str(ticket.get("subject") or "")
    description_text = _prepare_prompt_text(ticket.get("description"))
    description = description_text or "No description provided."
    status_value = str(ticket.get("status") or "open")
    priority_value = str(ticket.get("priority") or "normal")

    lines: list[str] = [_PROMPT_HEADER, "", f"Ticket subject: {subject}"]
    lines.append(f"Ticket status: {status_value}")
    lines.append(f"Ticket priority: {priority_value}")
    lines.append("Ticket description:")
    lines.append(description)
    lines.append("")
    lines.append("Conversation history (newest first):")

    trimmed = list(replies[-12:])
    trimmed.reverse()
    if not trimmed:
        lines.append("- No replies have been posted yet.")
    else:
        for reply in trimmed:
            created_at = reply.get("created_at")
            if hasattr(created_at, "isoformat"):
                timestamp = created_at.astimezone(timezone.utc).isoformat()
            else:
                timestamp = "unknown"
            author_id = reply.get("author_id")
            author_record = user_lookup.get(author_id) if isinstance(author_id, int) else None
            author_label = str(author_record.get("email") or author_record.get("first_name") or "User") if author_record else "User"
            visibility = "internal note" if reply.get("is_internal") else "public reply"
            body_text = _prepare_prompt_text(reply.get("body"))
            lines.append(f"- {timestamp} • {author_label} ({visibility}): {body_text}")

    lines.append("")
    lines.append(
        "Respond with JSON like {\"summary\": \"concise summary\", \"resolution\": \"Likely In Progress\"}."
    )
    return "\n".join(lines)


def _render_tags_prompt(
    ticket: Mapping[str, Any],
    replies: list[Mapping[str, Any]],
    user_lookup: Mapping[int, Mapping[str, Any]],
) -> str:
    subject = str(ticket.get("subject") or "")
    description_text = _prepare_prompt_text(ticket.get("description"))
    description = description_text or "No description provided."
    status_value = str(ticket.get("status") or "open")
    priority_value = str(ticket.get("priority") or "normal")
    category_value = str(ticket.get("category") or "uncategorised")
    module_value = str(ticket.get("module_slug") or "general")

    lines: list[str] = [_TAGS_PROMPT_HEADER, "", f"Ticket subject: {subject}"]
    lines.append(f"Ticket status: {status_value}")
    lines.append(f"Ticket priority: {priority_value}")
    lines.append(f"Ticket category: {category_value}")
    lines.append(f"Ticket module: {module_value}")
    lines.append("Ticket description:")
    lines.append(description)
    lines.append("")
    lines.append("Conversation highlights (newest first):")

    trimmed = list(replies[-12:])
    trimmed.reverse()
    if not trimmed:
        lines.append("- No replies have been posted yet.")
    else:
        for reply in trimmed:
            created_at = reply.get("created_at")
            if hasattr(created_at, "isoformat"):
                timestamp = created_at.astimezone(timezone.utc).isoformat()
            else:
                timestamp = "unknown"
            author_id = reply.get("author_id")
            author_record = user_lookup.get(author_id) if isinstance(author_id, int) else None
            author_label = (
                str(author_record.get("email") or author_record.get("first_name") or "User")
                if author_record
                else "User"
            )
            visibility = "internal note" if reply.get("is_internal") else "public reply"
            body_text = _prepare_prompt_text(reply.get("body"))
            lines.append(f"- {timestamp} • {author_label} ({visibility}): {body_text}")

    lines.append("")
    lines.append(
        "Return JSON containing a 'tags' array of 5 to 10 unique lowercase kebab-case strings that best describe the ticket."
    )
    return "\n".join(lines)
def _strip_wrapped_block(text: str) -> str:
    """Remove common Markdown or triple-quoted wrappers from a response."""

    stripped = text.strip()
    if not stripped:
        return stripped

    def _strip_language_preamble(body: str) -> str:
        body = body.lstrip("\n")
        if "\n" not in body:
            return body.strip()
        first_line, rest = body.split("\n", 1)
        candidate = first_line.strip()
        if candidate and not candidate.startswith("{") and not candidate.startswith("["):
            if all(ch.isalnum() or ch in {"-", "_", "."} for ch in candidate):
                return rest.strip()
        return body.strip()

    wrappers: tuple[tuple[str, bool], ...] = (
        ("```", True),
        ("~~~", True),
        ('"""', True),
        ("'''", True),
    )

    for fence, remove_language in wrappers:
        if stripped.startswith(fence) and stripped.endswith(fence) and len(stripped) >= len(fence) * 2:
            inner = stripped[len(fence) : -len(fence)]
            inner = inner.strip()
            if remove_language:
                inner = _strip_language_preamble(inner)
            return inner.strip()

    for fence, remove_language in wrappers:
        start = stripped.find(fence)
        end = stripped.rfind(fence)
        if start != -1 and end != -1 and end > start + len(fence):
            candidate = stripped[start : end + len(fence)]
            cleaned = _strip_wrapped_block(candidate)
            if cleaned != candidate.strip():
                return cleaned

    return stripped


def _parse_json_candidate(candidate: str) -> tuple[str | None, str | None] | None:
    if not candidate:
        return None

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, MappingABC):
        summary_candidate = (
            parsed.get("summary")
            or parsed.get("analysis")
            or parsed.get("text")
        )
        if isinstance(summary_candidate, str):
            summary_text = summary_candidate.strip()
        elif summary_candidate is None:
            summary_text = None
        else:
            summary_text = str(summary_candidate)
        if isinstance(summary_text, str) and not summary_text:
            summary_text = None
        resolution_candidate = (
            parsed.get("resolution")
            or parsed.get("resolution_label")
            or parsed.get("status")
            or parsed.get("state")
        )
        resolution_label = resolution_candidate.strip() if isinstance(resolution_candidate, str) else None
        if not summary_text:
            other_items = {
                key: value
                for key, value in parsed.items()
                if key not in {"resolution", "resolution_label", "status", "state"}
            }
            if other_items:
                summary_text = json.dumps(other_items, ensure_ascii=False)
        return summary_text, resolution_label

    if isinstance(parsed, str):
        cleaned = parsed.strip()
        return (cleaned or None, None)

    return candidate, None


def _extract_summary_fields(payload: Any) -> tuple[str | None, str | None]:
    if isinstance(payload, Mapping):
        direct_summary = payload.get("summary")
        direct_resolution = payload.get("resolution") or payload.get("resolution_label") or payload.get("status")
        if isinstance(direct_summary, str) or direct_summary is None:
            summary_text = direct_summary.strip() if isinstance(direct_summary, str) else None
        else:
            summary_text = str(direct_summary)
        if isinstance(summary_text, str) and not summary_text:
            summary_text = None
        if isinstance(direct_resolution, str):
            resolution_label = direct_resolution.strip()
        else:
            resolution_label = None
        if summary_text or resolution_label:
            return summary_text, resolution_label
        payload_text = payload.get("response") or payload.get("message")
    else:
        payload_text = payload

    text = str(payload_text).strip() if payload_text is not None else ""
    if not text:
        return None, None

    cleaned = _strip_wrapped_block(text)

    for candidate in (cleaned, text) if cleaned != text else (text,):
        result = _parse_json_candidate(candidate)
        if result is not None:
            return result

    if cleaned != text:
        return cleaned, None

    return text, None


def _normalise_resolution_state(label: str | None) -> str | None:
    if not label:
        return None
    lowered = label.lower()
    if "resolved" in lowered or "complete" in lowered or "fixed" in lowered or "closed" in lowered:
        return "likely_resolved"
    if "progress" in lowered or "working" in lowered or "pending" in lowered or "open" in lowered:
        return "likely_in_progress"
    if "in progress" in lowered:
        return "likely_in_progress"
    return None


def _extract_tags(payload: Any, ticket: Mapping[str, Any]) -> list[str]:
    tags = _normalise_tag_list(payload)
    if tags:
        return _finalise_tags(tags, ticket)
    if isinstance(payload, Mapping):
        nested = payload.get("response") or payload.get("message")
        tags = _normalise_tag_list(nested)
        if tags:
            return _finalise_tags(tags, ticket)
    text = str(payload).strip() if payload is not None else ""
    if not text:
        return _finalise_tags([], ticket)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        tags = _normalise_tag_list(text)
    else:
        tags = _normalise_tag_list(parsed)
    return _finalise_tags(tags, ticket)


def _normalise_tag_list(source: Any) -> list[str]:
    if source is None:
        return []
    if isinstance(source, Mapping):
        for key in ("tags", "keywords", "labels", "topics"):
            if key in source:
                return _normalise_tag_list(source[key])
        return []
    if isinstance(source, str):
        segments = [segment.strip() for segment in re.split(r"[,\n;]+", source) if segment.strip()]
        iterable: Iterable[str] = segments
    elif isinstance(source, Iterable) and not isinstance(source, (bytes, bytearray)):
        iterable = source
    else:
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in iterable:
        text = str(item).strip()
        slug = slugify_tag(text)
        if slug is None or not is_helpful_slug(slug):
            continue
        if slug in seen:
            continue
        tags.append(slug)
        seen.add(slug)
    return tags


def _finalise_tags(tags: list[str], ticket: Mapping[str, Any]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for tag in filter_helpful_slugs(tags):
        if tag in seen:
            continue
        unique.append(tag)
        seen.add(tag)
        if len(unique) >= 10:
            return unique[:10]

    for candidate in _generate_candidate_tags(ticket):
        if len(unique) >= 10:
            break
        if candidate in seen or not is_helpful_slug(candidate):
            continue
        unique.append(candidate)
        seen.add(candidate)

    for fallback in _DEFAULT_TAG_FILL:
        if len(unique) >= 5:
            break
        if fallback in seen or not is_helpful_slug(fallback):
            continue
        unique.append(fallback)
        seen.add(fallback)

    if len(unique) < 5:
        for fallback in _DEFAULT_TAG_FILL:
            if len(unique) >= 5:
                break
            if fallback in seen or not is_helpful_slug(fallback):
                continue
            unique.append(fallback)
            seen.add(fallback)

    return unique[:10]


def _generate_candidate_tags(ticket: Mapping[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("category", "module_slug", "priority", "status"):
        value = ticket.get(key)
        if isinstance(value, str):
            slug = slugify_tag(value)
            if slug and is_helpful_slug(slug):
                candidates.append(slug)
    subject = str(ticket.get("subject") or "")
    description = _prepare_prompt_text(ticket.get("description"))
    for word in re.findall(r"[A-Za-z0-9]+", subject):
        if len(word) < 3:
            continue
        slug = slugify_tag(word)
        if slug and is_helpful_slug(slug):
            candidates.append(slug)
    for word in re.findall(r"[A-Za-z0-9]+", description):
        if len(word) < 5:
            continue
        slug = slugify_tag(word)
        if slug and is_helpful_slug(slug):
            candidates.append(slug)
        if len(candidates) >= 25:
            break
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        deduped.append(candidate)
        seen.add(candidate)
    return deduped


@dataclass(slots=True)
class TicketDashboardState:
    tickets: list[TicketRecord]
    total: int
    status_counts: Counter[str]
    available_statuses: list[str]
    modules: list[Mapping[str, Any]]
    companies: list[Mapping[str, Any]]
    technicians: list[Mapping[str, Any]]
    company_lookup: dict[int, dict[str, Any]]
    user_lookup: dict[int, dict[str, Any]]


async def broadcast_ticket_event(
    *,
    action: str,
    ticket_id: int | None = None,
    notifier: RefreshNotifier | None = None,
) -> None:
    """Broadcast a realtime notification for ticket list updates."""

    normalised_action = (action or "").strip()
    if not normalised_action:
        return

    resolved_notifier = notifier or refresh_notifier
    data: dict[str, Any] = {"action": normalised_action}
    reason_parts = ["tickets", normalised_action]

    try:
        numeric_id = int(ticket_id) if ticket_id is not None else None
    except (TypeError, ValueError):
        numeric_id = None

    if numeric_id and numeric_id > 0:
        data["ticketId"] = numeric_id
        reason_parts.append(str(numeric_id))

    reason = ":".join(reason_parts)

    await resolved_notifier.broadcast_refresh(
        reason=reason,
        topics=("tickets",),
        data=data,
    )


async def load_dashboard_state(
    *,
    status_filter: str | None = None,
    module_filter: str | None = None,
    company_id: int | None = None,
    assigned_user_id: int | None = None,
    search: str | None = None,
    requester_id: int | None = None,
    limit: int = 200,
) -> TicketDashboardState:
    """Load ticket dashboard data used by the admin workspace."""

    tickets = await tickets_repo.list_tickets(
        status=status_filter,
        module_slug=module_filter,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        search=search,
        requester_id=requester_id,
        limit=limit,
    )
    total = await tickets_repo.count_tickets(
        status=status_filter,
        module_slug=module_filter,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        search=search,
        requester_id=requester_id,
    )

    status_counts = Counter((str(ticket.get("status") or "open")).lower() for ticket in tickets)
    available_statuses = sorted(
        {"open", "in_progress", "pending", "resolved", "closed", *status_counts.keys()}
    )

    modules = await modules_service.list_modules()
    companies = await company_repo.list_companies()
    technicians = await membership_repo.list_users_with_permission(HELPDESK_PERMISSION_KEY)

    company_lookup: dict[int, dict[str, Any]] = {}
    for company in companies:
        identifier = company.get("id")
        try:
            numeric_id = int(identifier)
        except (TypeError, ValueError):
            continue
        company_lookup[numeric_id] = company

    users = await user_repo.list_users()
    user_lookup: dict[int, dict[str, Any]] = {}
    for record in users:
        identifier = record.get("id")
        try:
            numeric_id = int(identifier)
        except (TypeError, ValueError):
            continue
        user_lookup[numeric_id] = record

    return TicketDashboardState(
        tickets=tickets,
        total=total,
        status_counts=status_counts,
        available_statuses=available_statuses,
        modules=modules,
        companies=companies,
        technicians=technicians,
        company_lookup=company_lookup,
        user_lookup=user_lookup,
    )
