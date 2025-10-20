from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from app.core.logging import log_error
from app.repositories import tickets as tickets_repo
from app.repositories import users as user_repo
from app.services import modules as modules_service

_PROMPT_HEADER = (
    "You are an AI assistant that summarises helpdesk tickets for technicians. "
    "Return a compact JSON object with the keys 'summary' and 'resolution'. "
    "The 'resolution' value must be either 'Likely Resolved' or 'Likely In Progress'. "
    "Only choose 'Likely Resolved' when the conversation clearly shows the issue has been addressed or the requester confirmed resolution. "
    "If there is not enough evidence of resolution, respond with 'Likely In Progress'."
)


async def refresh_ticket_ai_summary(ticket_id: int) -> None:
    """Refresh the Ollama-generated summary for a ticket if the module is configured."""

    try:
        ticket = await tickets_repo.get_ticket(ticket_id)
    except RuntimeError as exc:  # pragma: no cover - defensive for missing database
        log_error("Ticket AI summary skipped", ticket_id=ticket_id, error=str(exc))
        return
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

    prompt = _render_prompt(ticket, replies, user_lookup)
    now = datetime.now(timezone.utc)

    try:
        response = await modules_service.trigger_module("ollama", {"prompt": prompt})
    except ValueError:
        await tickets_repo.update_ticket(
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
        await tickets_repo.update_ticket(
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

    await tickets_repo.update_ticket(
        ticket_id,
        ai_summary=summary_text,
        ai_summary_status=status_value,
        ai_summary_model=str(model_value) if isinstance(model_value, str) else None,
        ai_resolution_state=resolution_state,
        ai_summary_updated_at=now,
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
    lines.append("Conversation history (newest last):")

    trimmed = replies[-12:]
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

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text, None

    if isinstance(parsed, Mapping):
        summary_candidate = parsed.get("summary") or parsed.get("analysis") or parsed.get("text")
        summary_text = (
            summary_candidate.strip() if isinstance(summary_candidate, str) else str(summary_candidate) if summary_candidate else None
        )
        if summary_text:
            summary_text = summary_text.strip()
        resolution_candidate = parsed.get("resolution") or parsed.get("resolution_label") or parsed.get("status") or parsed.get("state")
        resolution_label = resolution_candidate.strip() if isinstance(resolution_candidate, str) else None
        if not summary_text:
            other_items = {k: v for k, v in parsed.items() if k not in {"resolution", "resolution_label", "status", "state"}}
            if other_items:
                summary_text = json.dumps(other_items, ensure_ascii=False)
        return summary_text, resolution_label

    if isinstance(parsed, str):
        return parsed.strip() or None, None

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
