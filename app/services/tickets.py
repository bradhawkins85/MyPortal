from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping as MappingABC
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.core.database import db
from app.core.logging import log_error
from app.repositories import tickets as tickets_repo
from app.repositories import users as user_repo
from app.services import modules as modules_service
from app.services.tagging import filter_helpful_slugs, is_helpful_slug, slugify_tag

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


async def _safely_call(async_fn: Callable[..., Awaitable[Any]], *args, **kwargs):
    try:
        return await async_fn(*args, **kwargs)
    except RuntimeError as error:
        message = str(error)
        if "Database pool not initialised" in message or not db.is_connected():
            return None
        raise


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

    try:
        response = await modules_service.trigger_module("ollama", {"prompt": prompt})
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
    model_value = response.get("model")
    payload = response.get("response")

    summary_text, resolution_label = _extract_summary_fields(payload)
    resolution_state = _normalise_resolution_state(resolution_label)

    if status_value == "succeeded" and summary_text and not resolution_state:
        resolution_state = "likely_in_progress"

    await _safely_call(
        tickets_repo.update_ticket,
        ticket_id,
        ai_summary=summary_text,
        ai_summary_status=status_value,
        ai_summary_model=str(model_value) if isinstance(model_value, str) else None,
        ai_resolution_state=resolution_state,
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

    try:
        response = await modules_service.trigger_module("ollama", {"prompt": prompt})
    except ValueError:
        await tickets_repo.update_ticket(
            ticket_id,
            ai_tags=None,
            ai_tags_status="skipped",
            ai_tags_model=None,
            ai_tags_updated_at=now,
        )
        return
    except Exception as exc:  # pragma: no cover - network interaction
        log_error("Ticket AI tags failed", ticket_id=ticket_id, error=str(exc))
        await tickets_repo.update_ticket(
            ticket_id,
            ai_tags=None,
            ai_tags_status="error",
            ai_tags_model=None,
            ai_tags_updated_at=now,
        )
        return

    status_value = str(response.get("status") or "unknown")
    model_value = response.get("model")
    payload = response.get("response")

    tags: list[str] | None = None
    if status_value == "succeeded":
        tags = _extract_tags(payload, ticket)
    await tickets_repo.update_ticket(
        ticket_id,
        ai_tags=tags,
        ai_tags_status=status_value,
        ai_tags_model=str(model_value) if isinstance(model_value, str) else None,
        ai_tags_updated_at=now,
    )


def _render_prompt(
    ticket: Mapping[str, Any],
    replies: list[Mapping[str, Any]],
    user_lookup: Mapping[int, Mapping[str, Any]],
) -> str:
    subject = str(ticket.get("subject") or "")
    description = str(ticket.get("description") or "No description provided.")
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
            body = str(reply.get("body") or "").strip()
            lines.append(f"- {timestamp} • {author_label} ({visibility}): {body}")

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
    description = str(ticket.get("description") or "No description provided.")
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
            body = str(reply.get("body") or "").strip()
            lines.append(f"- {timestamp} • {author_label} ({visibility}): {body}")

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
    description = str(ticket.get("description") or "")
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
