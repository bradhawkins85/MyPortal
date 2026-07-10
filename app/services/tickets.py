from __future__ import annotations

import asyncio
import html
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping as MappingABC, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from app.core.config import get_settings
from app.core.database import db
from app.core.logging import log_error
from app.repositories import tickets as tickets_repo
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import ticket_statuses as ticket_status_repo
from app.repositories import staff as staff_repo
from app.repositories.tickets import TicketRecord
from app.services import automations as automations_service
from app.repositories import users as user_repo
from app.services import modules as modules_service
from app.services.tagging import filter_helpful_slugs, get_all_excluded_tags, is_helpful_slug, slugify_tag
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
    "You are an AI assistant that analyses customer helpdesk ticket messages and suggests between five and ten short tags. "
    "Tags must describe the customer-reported issues, affected systems, impacted users, and requested actions. "
    "Do not use technician replies, internal notes, automated assistant replies, or support-side troubleshooting steps as tag evidence. "
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


_TICKET_UPDATE_ACTOR_LABELS: dict[str, str] = {
    "system": "System",
    "automation": "Automation",
    "requester": "Requester",
    "watcher": "Watcher",
    "technician": "Technician",
}



_MAX_TICKET_DESCRIPTION_BYTES = 65_535
_TRUNCATION_NOTICE = "\n\n[Message truncated due to size]"


@dataclass(slots=True)
class TicketStatusDefinition:
    tech_status: str
    tech_label: str
    public_status: str
    is_default: bool = False
    hide_from_technicians: bool = False


def _build_status_definitions(records: Sequence[Mapping[str, Any]]) -> list[TicketStatusDefinition]:
    definitions: list[TicketStatusDefinition] = []
    for record in records:
        slug = str(record.get("tech_status") or "").strip().lower()
        if not slug:
            continue
        label = str(record.get("tech_label") or "").strip() or slug.replace("_", " ").title()
        public_status = str(record.get("public_status") or "").strip() or label
        is_default = bool(record.get("is_default", False))
        hide_from_technicians = bool(record.get("hide_from_technicians", False))
        definitions.append(
            TicketStatusDefinition(
                tech_status=slug,
                tech_label=label,
                public_status=public_status,
                is_default=is_default,
                hide_from_technicians=hide_from_technicians,
            )
        )
    return definitions


def _default_status_records() -> list[dict[str, str]]:
    return [
        {
            "tech_status": item["tech_status"],
            "tech_label": item["tech_label"],
            "public_status": item["public_status"],
            "hide_from_technicians": item.get("hide_from_technicians", False),
        }
        for item in ticket_status_repo.DEFAULT_STATUS_DEFINITIONS
    ]


async def list_status_definitions() -> list[TicketStatusDefinition]:
    try:
        records = await ticket_status_repo.list_statuses()
    except RuntimeError as exc:
        if "Database pool not initialised" in str(exc):
            records = _default_status_records()
        else:
            raise
    if not records:
        try:
            records = await ticket_status_repo.ensure_default_statuses()
        except RuntimeError as exc:
            if "Database pool not initialised" in str(exc):
                records = _default_status_records()
            else:
                raise
    if not records:
        records = _default_status_records()
    return _build_status_definitions(records)


async def get_status_label_map() -> dict[str, str]:
    return {definition.tech_status: definition.tech_label for definition in await list_status_definitions()}


async def get_public_status_map() -> dict[str, str]:
    return {definition.tech_status: definition.public_status for definition in await list_status_definitions()}


async def replace_ticket_statuses(status_inputs: Sequence[Mapping[str, Any]]) -> list[TicketStatusDefinition]:
    if not status_inputs:
        raise ValueError("At least one ticket status must be provided.")

    cleaned: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    encountered_originals: set[str] = set()
    has_default = False

    for index, definition in enumerate(status_inputs):
        tech_label = str(
            definition.get("tech_label")
            or definition.get("techLabel")
            or definition.get("label")
            or ""
        ).strip()
        if not tech_label:
            raise ValueError("Tech status labels cannot be empty.")
        if len(tech_label) > 128:
            raise ValueError("Tech status labels must be 128 characters or fewer.")

        public_status = str(
            definition.get("public_status")
            or definition.get("publicStatus")
            or ""
        ).strip()
        if not public_status:
            public_status = tech_label
        if len(public_status) > 128:
            raise ValueError("Public status labels must be 128 characters or fewer.")

        original_slug_raw = str(
            definition.get("original_slug")
            or definition.get("existing_slug")
            or definition.get("existingSlug")
            or definition.get("tech_status")
            or definition.get("techStatus")
            or ""
        ).strip()

        is_default = bool(
            definition.get("is_default")
            or definition.get("isDefault")
            or False
        )
        hide_from_technicians = bool(
            definition.get("hide_from_technicians")
            or definition.get("hideFromTechnicians")
            or False
        )

        if is_default:
            if has_default:
                raise ValueError("Only one status can be set as default.")
            has_default = True

        slug = ticket_status_repo.slugify_status_label(tech_label)
        if not slug:
            raise ValueError("Tech status labels must include letters or numbers.")

        original_slug = (
            ticket_status_repo.slugify_status_label(original_slug_raw)
            if original_slug_raw
            else None
        )

        if original_slug and original_slug in encountered_originals:
            raise ValueError("Tech status values must be unique.")
        if slug in seen_slugs and (original_slug is None or slug != original_slug):
            raise ValueError("Tech status values must be unique.")
        if original_slug:
            encountered_originals.add(original_slug)
        seen_slugs.add(slug)

        cleaned.append(
            {
                "tech_status": slug,
                "tech_label": tech_label,
                "public_status": public_status,
                "original_slug": original_slug or slug,
                "is_default": is_default,
                "hide_from_technicians": hide_from_technicians,
            }
        )

    # If no status was marked as default, mark the first one as default
    if not has_default and cleaned:
        cleaned[0]["is_default"] = True

    records = await ticket_status_repo.replace_statuses(cleaned)
    return _build_status_definitions(records)


async def validate_status_choice(value: str, *, allow_hidden: bool = True) -> str:
    slug = ticket_status_repo.slugify_status_label(value)
    if not slug:
        raise ValueError("Select a status to apply.")
    definition = await ticket_status_repo.get_status_definition(slug)
    if not definition:
        raise ValueError("Select a valid status to apply.")
    if definition.get("hide_from_technicians") and not allow_hidden:
        raise ValueError("Select a ticket status available to technicians.")
    return slug


async def resolve_status_or_default(value: str | None) -> str:
    slug = ticket_status_repo.slugify_status_label(value or "")
    if slug and await ticket_status_repo.status_exists(slug):
        return slug
    # Try to get the default status
    default_status = await ticket_status_repo.get_default_status()
    if default_status:
        return default_status["tech_status"]
    # Fall back to first status in list
    definitions = await list_status_definitions()
    if definitions:
        return definitions[0].tech_status
    return "open"


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


def format_reply_time_summary(
    minutes_spent: int | None,
    is_billable: bool,
    labour_type_name: str | None = None,
) -> str | None:
    """Return a compact human readable summary for reply time tracking."""

    if minutes_spent is None:
        return None
    try:
        minutes_value = int(minutes_spent)
    except (TypeError, ValueError):
        return None
    if minutes_value < 0:
        return None
    label = "minute" if minutes_value == 1 else "minutes"
    billing_label = "Billable" if is_billable else "Non-billable"
    summary = f"{minutes_value} {label} · {billing_label}"
    labour_label = (labour_type_name or "").strip()
    if labour_label:
        summary = f"{summary} · {labour_label}"
    return summary


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



def _extract_sms_recipient_from_external_reference(value: Any) -> str | None:
    """Return the mobile number embedded in an SMS ticket external reference."""

    if value is None:
        return None
    raw = str(value).strip()
    if not raw.lower().startswith("sms:"):
        return None
    parts = raw.split(":", 2)
    if len(parts) < 2:
        return None
    recipient = parts[1].strip()
    return recipient or None


def _attach_sms_context(ticket: TicketRecord) -> None:
    recipient = _extract_sms_recipient_from_external_reference(ticket.get("external_reference"))
    ticket["sms"] = {"recipient": recipient} if recipient else None


async def _enrich_ticket_context(ticket: Mapping[str, Any]) -> TicketRecord:
    enriched: TicketRecord = dict(ticket)

    # Normalise ticket number fields so templates always have access.
    # Prefer an explicitly-stored ticket_number; fall back to any pre-existing
    # "number" alias (e.g. set by a caller), then to the database row id so
    # that locally-created tickets (which have no external ticket_number) still
    # render correctly in templates.
    ticket_number = (
        enriched.get("ticket_number")
        or enriched.get("number")
        or (str(enriched["id"]) if enriched.get("id") is not None else None)
    )

    # Expose both names so {{ticket.number}} and {{ticket.ticket_number}} work.
    enriched["ticket_number"] = ticket_number
    enriched["number"] = ticket_number
    
    # Add 'labels' alias for 'ai_tags' to support {{ticket.labels}} template variable
    if "labels" not in enriched:
        enriched["labels"] = enriched.get("ai_tags") or []

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
        staff_snapshot = None
        requester_staff_id = enriched.get("requester_staff_id")
        try:
            staff_record = (
                await staff_repo.get_staff_by_id(int(requester_staff_id))
                if requester_staff_id is not None
                else None
            )
        except (TypeError, ValueError):
            staff_record = None
        if isinstance(staff_record, Mapping):
            staff_snapshot = _build_user_snapshot(staff_record)
        if staff_snapshot:
            enriched["requester"] = staff_snapshot
            enriched["requester_email"] = staff_snapshot.get("email")
            enriched["requester_display_name"] = staff_snapshot.get("display_name")
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
        watcher_email = snapshot.get("email") if snapshot else watcher.get("email")
        entry: dict[str, Any] = {
            "id": watcher.get("id"),
            "ticket_id": watcher.get("ticket_id"),
            "user_id": watcher.get("user_id"),
            "created_at": watcher.get("created_at"),
            "user": snapshot,
            "email": str(watcher_email).strip() if watcher_email else None,
            "display_name": snapshot.get("display_name") if snapshot else watcher.get("email"),
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
        snapshot_email = author_snapshot.get("email") if author_snapshot else None
        snapshot_display = author_snapshot.get("display_name") if author_snapshot else None
        reply["author_email"] = snapshot_email or reply.get("author_email")
        reply["author_display_name"] = snapshot_display or reply.get("author_display_name")
        latest_reply = reply
    enriched["latest_reply"] = latest_reply

    if isinstance(ticket_id, int):
        filter_context = await _safely_call(tickets_repo.get_automation_filter_context, ticket_id)
        if isinstance(filter_context, Mapping):
            enriched.update(filter_context)
    enriched.setdefault("billable_minutes", 0)
    enriched.setdefault("non_billable_minutes", 0)
    enriched.setdefault("attachment_count", 0)
    enriched.setdefault("has_attachments", False)
    enriched.setdefault("task_count", 0)
    enriched.setdefault("has_tasks", False)
    enriched.setdefault("open_task_count", 0)
    enriched.setdefault("has_open_tasks", False)

    _attach_sms_context(enriched)

    return enriched


def _normalise_ticket_update_actor(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    candidate = str(value).strip().lower()
    if candidate in _TICKET_UPDATE_ACTOR_LABELS:
        return candidate
    return None


def _normalise_email_for_compare(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip().lower()
    return candidate or None


def _auto_detect_ticket_update_actor(
    ticket: Mapping[str, Any],
    actor: Mapping[str, Any] | None,
) -> str:
    actor_id = None
    actor_email = None
    if isinstance(actor, Mapping):
        try:
            actor_id = int(actor.get("id")) if actor.get("id") is not None else None
        except (TypeError, ValueError):
            actor_id = None
        actor_email = _normalise_email_for_compare(actor.get("email"))
    if actor_id is not None:
        requester_id = ticket.get("requester_id")
        if requester_id is not None and actor_id == requester_id:
            return "requester"
        assigned_id = ticket.get("assigned_user_id")
        if assigned_id is not None and actor_id == assigned_id:
            return "technician"
        watchers = ticket.get("watchers")
        if isinstance(watchers, Sequence):
            for watcher in watchers:
                if not isinstance(watcher, Mapping):
                    continue
                watcher_user_id = watcher.get("user_id")
                try:
                    watcher_user_id_int = int(watcher_user_id)
                except (TypeError, ValueError):
                    continue
                if watcher_user_id_int == actor_id:
                    return "watcher"
    if actor_email:
        requester_email = _normalise_email_for_compare(ticket.get("requester_email"))
        if requester_email and actor_email == requester_email:
            return "requester"
        watchers = ticket.get("watchers")
        if isinstance(watchers, Sequence):
            for watcher in watchers:
                if not isinstance(watcher, Mapping):
                    continue
                watcher_email = _normalise_email_for_compare(watcher.get("email"))
                if watcher_email and actor_email == watcher_email:
                    return "watcher"
    return "system"


async def _emit_ticket_event(
    event_name: str,
    ticket: Mapping[str, Any] | int,
    *,
    actor_type: str | None = None,
    actor: Mapping[str, Any] | None = None,
    reply: Mapping[str, Any] | None = None,
    trigger_automations: bool = True,
) -> None:
    """Shared helper that builds ticket context and fires an automation event."""

    ticket_record: Mapping[str, Any] | None
    if isinstance(ticket, Mapping):
        ticket_record = ticket
    else:
        try:
            ticket_id = int(ticket)
        except (TypeError, ValueError):
            return
        ticket_record = await tickets_repo.get_ticket(ticket_id)
    if not ticket_record:
        return

    if ticket_record.get("merged_into_ticket_id"):
        return

    enriched = await _enrich_ticket_context(ticket_record)

    actor_snapshot = _build_user_snapshot(actor)
    if actor_snapshot is None and isinstance(actor, Mapping):
        minimal: dict[str, Any] = {}
        if "id" in actor:
            minimal["id"] = actor.get("id")
        if "email" in actor:
            minimal["email"] = actor.get("email")
        if "display_name" in actor:
            minimal["display_name"] = actor.get("display_name")
        if "first_name" in actor or "last_name" in actor:
            display = _format_user_display_name(actor)
            if display:
                minimal["display_name"] = display
        actor_snapshot = minimal or None

    normalised_actor = _normalise_ticket_update_actor(actor_type)
    if normalised_actor is None:
        normalised_actor = _auto_detect_ticket_update_actor(enriched, actor_snapshot)
    actor_label = _TICKET_UPDATE_ACTOR_LABELS.get(normalised_actor, "System")

    metadata: dict[str, Any] = {
        "actor_type": normalised_actor,
        "actor_label": actor_label,
        "actor_user": actor_snapshot,
    }
    if isinstance(actor_snapshot, Mapping):
        metadata["actor_user_id"] = actor_snapshot.get("id")
        metadata["actor_user_email"] = actor_snapshot.get("email")
        metadata["actor_user_display_name"] = actor_snapshot.get("display_name")

    context = {
        "ticket": enriched,
        "ticket_update": metadata,
    }
    if isinstance(reply, Mapping):
        is_internal = bool(reply.get("is_internal"))
        reply_context = dict(reply)
        reply_context["is_internal"] = is_internal
        reply_context["kind"] = "internal_note" if is_internal else "message"
        context["reply"] = reply_context

    if not trigger_automations:
        return

    await automations_service.handle_event(event_name, context)


async def emit_ticket_replied_event(
    ticket: Mapping[str, Any] | int,
    *,
    actor_type: str | None = None,
    actor: Mapping[str, Any] | None = None,
    reply: Mapping[str, Any] | None = None,
    trigger_automations: bool = True,
) -> None:
    """Emit a ``tickets.replied`` automation event with actor metadata."""

    await _emit_ticket_event(
        "tickets.replied",
        ticket,
        actor_type=actor_type,
        actor=actor,
        reply=reply,
        trigger_automations=trigger_automations,
    )


async def emit_ticket_updated_event(
    ticket: Mapping[str, Any] | int,
    *,
    actor_type: str | None = None,
    actor: Mapping[str, Any] | None = None,
    reply: Mapping[str, Any] | None = None,
    trigger_automations: bool = True,
) -> None:
    """Emit a ``tickets.updated`` automation event with actor/reply metadata."""

    await _emit_ticket_event(
        "tickets.updated",
        ticket,
        actor_type=actor_type,
        actor=actor,
        reply=reply,
        trigger_automations=trigger_automations,
    )

    # Mirror ticket changes (subject/status/company) into the linked
    # Solidtime project. The sync helper is a no-op when the module is
    # disabled, so this remains safe in test/dev environments.
    ticket_id_value: int | None = None
    if isinstance(ticket, Mapping):
        candidate = ticket.get("id")
        if isinstance(candidate, int):
            ticket_id_value = candidate
    elif isinstance(ticket, int):
        ticket_id_value = ticket
    if ticket_id_value is not None and ticket_id_value > 0:
        try:
            from app.services import solidtime as solidtime_service

            solidtime_service.schedule_ticket_sync(ticket_id_value)
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to schedule Solidtime ticket sync on update",
                ticket_id=ticket_id_value,
                error=str(exc),
            )


async def emit_ticket_details_updated_event(
    ticket: Mapping[str, Any] | int,
    *,
    actor_type: str | None = None,
    actor: Mapping[str, Any] | None = None,
    trigger_automations: bool = True,
) -> None:
    """Emit a ``tickets.details_updated`` automation event with actor metadata."""

    await _emit_ticket_event(
        "tickets.details_updated",
        ticket,
        actor_type=actor_type,
        actor=actor,
        trigger_automations=trigger_automations,
    )


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
        await emit_ticket_updated_event(ticket_id, actor_type="system")

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
        await emit_ticket_updated_event(ticket_id, actor_type="system")
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
        await emit_ticket_updated_event(ticket_id, actor_type="system")
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
        await emit_ticket_updated_event(ticket_id, actor_type="system")


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
            tags = await _extract_tags(payload, ticket, replies)
        updated_at = datetime.now(timezone.utc)
        await _safely_call(
            tickets_repo.update_ticket,
            ticket_id,
            ai_tags=tags if status_value == "succeeded" else None,
            ai_tags_status=status_value,
            ai_tags_model=str(model_value) if isinstance(model_value, str) else None,
            ai_tags_updated_at=updated_at,
        )
        await emit_ticket_updated_event(ticket_id, actor_type="system")

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
        await emit_ticket_updated_event(ticket_id, actor_type="system")
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
        await emit_ticket_updated_event(ticket_id, actor_type="system")
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
        await emit_ticket_updated_event(ticket_id, actor_type="system")


async def _send_ticket_creation_email(
    enriched_ticket: Mapping[str, Any],
    *,
    requester_email_fallback: str | None = None,
) -> None:
    """Do not send default ticket creation notifications.

    New ticket notifications are intentionally handled exclusively by ticket
    automations (the ``tickets.created`` automation event emitted by
    :func:`create_ticket`).  This no-op helper is retained for compatibility
    with callers that still pass ``send_creation_notification=True`` while
    preventing the platform notification service or fallback email sender from
    producing default messages such as ``MyPortal notification: Your ticket``.
    """
    return None


async def create_ticket(
    *,
    subject: str,
    description: str | None,
    requester_id: int | None,
    company_id: int | None,
    requester_staff_id: int | None = None,
    assigned_user_id: int | None,
    priority: str,
    status: str,
    category: str | None,
    module_slug: str | None,
    external_reference: str | None,
    ticket_number: str | None = None,
    trigger_automations: bool = True,
    initial_reply_author_id: int | None = None,
    id: int | None = None,
    requester_email: str | None = None,
    send_creation_notification: bool = True,
    record_initial_reply: bool = True,
) -> TicketRecord:
    """Create a ticket and emit the corresponding automation event."""

    status_slug = await resolve_status_or_default(status)
    truncated_description = _truncate_description(description)

    original_description: str | None = None
    if description is not None:
        original_description = str(description)

    truncated_description = _truncate_description(original_description)

    ticket = await tickets_repo.create_ticket(
        subject=subject,
        description=truncated_description,
        requester_id=requester_id,
        requester_staff_id=requester_staff_id,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        priority=priority,
        status=status_slug,
        category=category,
        module_slug=module_slug,
        external_reference=external_reference,
        ticket_number=ticket_number,
        id=id,
    )

    ticket_id = ticket.get("id") if isinstance(ticket, Mapping) else None
    author_id: int | None = None
    if initial_reply_author_id is not None:
        try:
            author_id = int(initial_reply_author_id)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            author_id = None
        else:
            if author_id <= 0:
                author_id = None
    if (
        isinstance(ticket_id, int)
        and ticket_id > 0
        and record_initial_reply
        and isinstance(original_description, str)
        and original_description
    ):
        try:
            await tickets_repo.create_reply(
                ticket_id=ticket_id,
                author_id=author_id,
                body=original_description,
                is_internal=False,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to record initial ticket conversation entry",
                ticket_id=ticket_id,
                error=str(exc),
            )

    enriched_ticket = await _enrich_ticket_context(ticket)

    # New ticket notifications must be handled by Ticket Automations only.
    # ``send_creation_notification`` is retained for API compatibility but no
    # longer triggers default platform notification/email delivery.

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

    # Push to Solidtime as a project (no-op if module disabled).
    enriched_id = enriched_ticket.get("id")
    if isinstance(enriched_id, int) and enriched_id > 0:
        try:
            from app.services import solidtime as solidtime_service

            solidtime_service.schedule_ticket_sync(enriched_id)
        except Exception as exc:  # pragma: no cover - defensive logging
            log_error(
                "Failed to schedule Solidtime ticket sync",
                ticket_id=enriched_id,
                error=str(exc),
            )

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


def _is_customer_tag_reply(
    reply: Mapping[str, Any],
    ticket: Mapping[str, Any],
    user_lookup: Mapping[int, Mapping[str, Any]],
) -> bool:
    """Return whether a reply should be used as customer evidence for AI tags."""

    if reply.get("is_internal"):
        return False

    settings = get_settings()
    bot_user_id = str(settings.matrix_bot_user_id or "").strip()
    sender_matrix_id = str(reply.get("sender_matrix_id") or "").strip()
    if bot_user_id and sender_matrix_id and sender_matrix_id == bot_user_id:
        return False

    author_id = reply.get("author_id")
    requester_id = ticket.get("requester_id")
    if isinstance(requester_id, int):
        return author_id == requester_id

    author_record = user_lookup.get(author_id) if isinstance(author_id, int) else None
    if author_record:
        is_super_admin = bool(author_record.get("is_super_admin"))
        permissions = author_record.get("permissions") or []
        if isinstance(permissions, str):
            permissions = [permissions]
        if is_super_admin or HELPDESK_PERMISSION_KEY in set(permissions):
            return False

    return True

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
    lines.append("Customer conversation highlights (newest first):")

    customer_replies = [reply for reply in replies if _is_customer_tag_reply(reply, ticket, user_lookup)]
    trimmed = list(customer_replies[-12:])
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


def _extract_chat_completion_content(payload: Any) -> Any:
    """Return assistant message content from OpenAI-compatible chat responses.

    Several Ollama-compatible gateways return the whole chat completion object as
    the module response instead of only ``choices[0].message.content``.  The
    ticket importers need the actual assistant content so summary, resolution,
    and tag JSON can be parsed reliably.
    """

    if isinstance(payload, Mapping):
        choices = payload.get("choices")
        if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes, bytearray)):
            for choice in choices:
                if not isinstance(choice, Mapping):
                    continue
                message = choice.get("message")
                if isinstance(message, Mapping):
                    content = message.get("content")
                    if content is not None:
                        return content
                text = choice.get("text")
                if text is not None:
                    return text
        return payload

    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return payload
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return payload
        extracted = _extract_chat_completion_content(parsed)
        return extracted if extracted is not parsed else payload

    return payload

def _normalise_model_response_text(value: Any) -> str:
    """Return model text in a parseable plain-text form.

    Some integrations pass model output through HTML-oriented formatting before the
    ticket summary parser sees it.  Convert common line-break markup back to text
    so JSON such as ``{<br />"summary": ...}`` is parsed instead of displayed to
    technicians.
    """

    text = str(value).strip() if value is not None else ""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|tr|td|th|tbody|thead|ul|ol)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _extract_summary_fields(payload: Any) -> tuple[str | None, str | None]:
    payload = _extract_chat_completion_content(payload)
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

    text = _normalise_model_response_text(payload_text)
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


def _extract_external_reference_tokens(ticket: Mapping[str, Any], replies: list[Mapping[str, Any]]) -> set[str]:
    """Extract and normalize tokens from external_reference fields to prevent them from becoming tags."""
    tokens: set[str] = set()
    
    # Extract from ticket's external_reference
    ticket_ref = ticket.get("external_reference")
    if ticket_ref and isinstance(ticket_ref, str):
        # Extract alphanumeric tokens from the reference
        for token in re.findall(r"[A-Za-z0-9]+", ticket_ref):
            if len(token) >= 3:  # Only meaningful tokens
                slug = slugify_tag(token)
                if slug:
                    tokens.add(slug)
    
    # Extract from replies' external_reference
    for reply in replies:
        reply_ref = reply.get("external_reference")
        if reply_ref and isinstance(reply_ref, str):
            for token in re.findall(r"[A-Za-z0-9]+", reply_ref):
                if len(token) >= 3:
                    slug = slugify_tag(token)
                    if slug:
                        tokens.add(slug)
    
    return tokens


async def _extract_tags(payload: Any, ticket: Mapping[str, Any], replies: list[Mapping[str, Any]] | None = None) -> list[str]:
    excluded_tags = await get_all_excluded_tags()
    
    # Get external reference tokens to exclude
    external_ref_tokens = _extract_external_reference_tokens(ticket, replies or [])
    combined_exclusions = excluded_tags | external_ref_tokens
    
    payload = _extract_chat_completion_content(payload)

    tags = _normalise_tag_list(payload, combined_exclusions)
    if tags:
        return _finalise_tags(tags, ticket, combined_exclusions)
    if isinstance(payload, Mapping):
        nested = payload.get("response") or payload.get("message")
        tags = _normalise_tag_list(nested, combined_exclusions)
        if tags:
            return _finalise_tags(tags, ticket, combined_exclusions)
    text = str(payload).strip() if payload is not None else ""
    if not text:
        return _finalise_tags([], ticket, combined_exclusions)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        tags = _normalise_tag_list(text, combined_exclusions)
    else:
        tags = _normalise_tag_list(parsed, combined_exclusions)
    return _finalise_tags(tags, ticket, combined_exclusions)


def _normalise_tag_list(source: Any, excluded_tags: set[str] | None = None) -> list[str]:
    if source is None:
        return []
    if isinstance(source, Mapping):
        for key in ("tags", "keywords", "labels", "topics"):
            if key in source:
                return _normalise_tag_list(source[key], excluded_tags)
        return []
    if isinstance(source, str):
        cleaned = _strip_wrapped_block(_normalise_model_response_text(source))
        if cleaned and cleaned != source.strip():
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                source = cleaned
            else:
                return _normalise_tag_list(parsed, excluded_tags)
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
        if slug is None or not is_helpful_slug(slug, excluded_tags):
            continue
        if slug in seen:
            continue
        tags.append(slug)
        seen.add(slug)
    return tags


def _finalise_tags(tags: list[str], ticket: Mapping[str, Any], excluded_tags: set[str] | None = None) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for tag in filter_helpful_slugs(tags, excluded_tags):
        if tag in seen:
            continue
        unique.append(tag)
        seen.add(tag)
        if len(unique) >= 10:
            return unique[:10]

    for candidate in _generate_candidate_tags(ticket, excluded_tags):
        if len(unique) >= 10:
            break
        if candidate in seen or not is_helpful_slug(candidate, excluded_tags):
            continue
        unique.append(candidate)
        seen.add(candidate)

    for fallback in _DEFAULT_TAG_FILL:
        if len(unique) >= 5:
            break
        if fallback in seen or not is_helpful_slug(fallback, excluded_tags):
            continue
        unique.append(fallback)
        seen.add(fallback)

    if len(unique) < 5:
        for fallback in _DEFAULT_TAG_FILL:
            if len(unique) >= 5:
                break
            if fallback in seen or not is_helpful_slug(fallback, excluded_tags):
                continue
            unique.append(fallback)
            seen.add(fallback)

    return unique[:10]


def _generate_candidate_tags(ticket: Mapping[str, Any], excluded_tags: set[str] | None = None) -> list[str]:
    candidates: list[str] = []
    for key in ("category", "module_slug", "priority", "status"):
        value = ticket.get(key)
        if isinstance(value, str):
            slug = slugify_tag(value)
            if slug and is_helpful_slug(slug, excluded_tags):
                candidates.append(slug)
    subject = str(ticket.get("subject") or "")
    description = _prepare_prompt_text(ticket.get("description"))
    for word in re.findall(r"[A-Za-z0-9]+", subject):
        if len(word) < 3:
            continue
        slug = slugify_tag(word)
        if slug and is_helpful_slug(slug, excluded_tags):
            candidates.append(slug)
    for word in re.findall(r"[A-Za-z0-9]+", description):
        if len(word) < 5:
            continue
        slug = slugify_tag(word)
        if slug and is_helpful_slug(slug, excluded_tags):
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
    status_definitions: list[TicketStatusDefinition]
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
    status_filter: str | list[str] | None = None,
    module_filter: str | None = None,
    company_id: int | None = None,
    assigned_user_id: int | None = None,
    search: str | None = None,
    requester_id: int | None = None,
    limit: int | None = 200,
    include_reference_data: bool = True,
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
    status_definitions = await list_status_definitions()
    definition_slugs = [definition.tech_status for definition in status_definitions]
    total = await tickets_repo.count_tickets(
        status=status_filter,
        module_slug=module_filter,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        search=search,
        requester_id=requester_id,
    )

    status_counts: Counter[str] = Counter()
    for ticket in tickets:
        slug = ticket_status_repo.slugify_status_label(ticket.get("status") or "")
        if not slug and definition_slugs:
            slug = definition_slugs[0]
        elif not slug:
            slug = "open"
        status_counts[slug] += 1

    available_statuses = sorted({*definition_slugs, *status_counts.keys()})

    requester_staff_ids: set[int] = set()
    for ticket in tickets:
        identifier = ticket.get("requester_staff_id")
        try:
            if identifier is not None:
                requester_staff_ids.add(int(identifier))
        except (TypeError, ValueError):
            continue
    staff_lookup: dict[int, dict[str, Any]] = {}
    if requester_staff_ids:
        staff_results = await asyncio.gather(
            *(staff_repo.get_staff_by_id(staff_id) for staff_id in requester_staff_ids),
            return_exceptions=True,
        )
        for record in staff_results:
            if not isinstance(record, Mapping) or record.get("id") is None:
                continue
            try:
                staff_lookup[int(record["id"])] = dict(record)
            except (TypeError, ValueError):
                continue
    for ticket in tickets:
        requester_staff_id = ticket.get("requester_staff_id")
        try:
            staff_record = staff_lookup.get(int(requester_staff_id)) if requester_staff_id is not None else None
        except (TypeError, ValueError):
            staff_record = None
        if staff_record:
            name = " ".join(
                part
                for part in (
                    str(staff_record.get("first_name") or "").strip(),
                    str(staff_record.get("last_name") or "").strip(),
                )
                if part
            )
            ticket["requester_label"] = name or str(staff_record.get("email") or "").strip() or None
            ticket["requester_email"] = str(staff_record.get("email") or "").strip() or None

    modules: list[Mapping[str, Any]] = []
    companies: list[Mapping[str, Any]] = []
    technicians: list[Mapping[str, Any]] = []

    if include_reference_data:
        modules = await modules_service.list_modules()
        companies = await company_repo.list_companies()
        technicians = await membership_repo.list_users_with_permission(HELPDESK_PERMISSION_KEY)

    company_lookup: dict[int, dict[str, Any]] = {}
    if include_reference_data:
        for company in companies:
            identifier = company.get("id")
            try:
                numeric_id = int(identifier)
            except (TypeError, ValueError):
                continue
            company_lookup[numeric_id] = dict(company)
    else:
        company_ids: set[int] = set()
        for ticket in tickets:
            identifier = ticket.get("company_id")
            try:
                company_ids.add(int(identifier))
            except (TypeError, ValueError):
                continue
        if company_ids:
            company_results = await asyncio.gather(
                *(company_repo.get_company_by_id(company_id) for company_id in company_ids)
            )
            for record in company_results:
                if not record:
                    continue
                identifier = record.get("id")
                try:
                    numeric_id = int(identifier)
                except (TypeError, ValueError):
                    continue
                company_lookup[numeric_id] = record

    user_lookup: dict[int, dict[str, Any]] = {}
    if include_reference_data:
        users = await user_repo.list_users()
        for record in users:
            identifier = record.get("id")
            try:
                numeric_id = int(identifier)
            except (TypeError, ValueError):
                continue
            user_lookup[numeric_id] = record
    else:
        user_ids: set[int] = set()
        for ticket in tickets:
            for field_name in ("assigned_user_id", "requester_id"):
                identifier = ticket.get(field_name)
                try:
                    user_ids.add(int(identifier))
                except (TypeError, ValueError):
                    continue
        if user_ids:
            user_results = await asyncio.gather(
                *(user_repo.get_user_by_id(user_id) for user_id in user_ids)
            )
            for record in user_results:
                if not record:
                    continue
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
        status_definitions=status_definitions,
        modules=modules,
        companies=companies,
        technicians=technicians,
        company_lookup=company_lookup,
        user_lookup=user_lookup,
    )


async def split_ticket(
    original_ticket_id: int,
    reply_ids: list[int],
    new_subject: str,
) -> tuple[TicketRecord | None, TicketRecord | None, int]:
    """
    Split a ticket by moving selected replies to a new ticket.
    The new ticket will have the same company and requester as the original.
    """
    # Validate that all reply_ids belong to the original ticket (efficient single query)
    is_valid, error_message = await tickets_repo.validate_replies_belong_to_ticket(
        reply_ids, original_ticket_id
    )
    if not is_valid:
        raise ValueError(error_message)
    
    # Split the ticket using repository method
    original_ticket, new_ticket, moved_count = await tickets_repo.split_ticket(
        original_ticket_id=original_ticket_id,
        reply_ids=reply_ids,
        new_ticket_subject=new_subject,
        new_ticket_id=None,  # Let database auto-generate ID
    )
    
    # Emit events for both tickets
    if new_ticket:
        await emit_ticket_updated_event(
            new_ticket["id"],
            actor_type="system",
            actor={"id": 0, "email": "system"},
        )
        await broadcast_ticket_event(action="create", ticket_id=new_ticket["id"])
    
    if original_ticket:
        await emit_ticket_updated_event(
            original_ticket_id,
            actor_type="system",
            actor={"id": 0, "email": "system"},
        )
        await broadcast_ticket_event(action="update", ticket_id=original_ticket_id)
    
    return original_ticket, new_ticket, moved_count


async def merge_tickets(
    ticket_ids: list[int],
    target_ticket_id: int,
) -> tuple[TicketRecord | None, list[int], int]:
    """
    Merge multiple tickets into a target ticket.
    All replies and time entries are moved to the target ticket.
    Source tickets are marked as closed and merged.
    """
    if len(ticket_ids) < 2:
        raise ValueError("At least 2 tickets are required for merging")
    
    if target_ticket_id not in ticket_ids:
        raise ValueError("Target ticket must be one of the tickets being merged")
    
    # Validate all tickets exist
    for ticket_id in ticket_ids:
        ticket = await tickets_repo.get_ticket(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")
    
    # Merge tickets using repository method
    merged_ticket, merged_ids, moved_count = await tickets_repo.merge_tickets(
        ticket_ids=ticket_ids,
        target_ticket_id=target_ticket_id,
    )
    
    # Emit events for merged ticket
    if merged_ticket:
        await emit_ticket_updated_event(
            target_ticket_id,
            actor_type="system",
            actor={"id": 0, "email": "system"},
        )
        await broadcast_ticket_event(action="update", ticket_id=target_ticket_id)
    
    # Do not emit automation events for child tickets once merged; broadcast only
    # so any open UI rows can disappear from lists that exclude merged tickets.
    for ticket_id in merged_ids:
        await broadcast_ticket_event(action="update", ticket_id=ticket_id)
    
    return merged_ticket, merged_ids, moved_count
