from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.config import get_settings
from app.core.logging import log_error
from app.services import modules as modules_service


def _chat_subject(room: Mapping[str, Any] | None, fallback: str = "Support chat") -> str:
    subject = str((room or {}).get("subject") or fallback or "Support chat").strip()
    return subject[:500] or "Support chat"


def _sender_name(user: Mapping[str, Any] | None, message: Mapping[str, Any] | None = None) -> str:
    if user:
        display = str(user.get("display_name") or "").strip()
        if display:
            return display[:200]
        first = str(user.get("first_name") or "").strip()
        last = str(user.get("last_name") or "").strip()
        full_name = " ".join(part for part in (first, last) if part).strip()
        if full_name:
            return full_name[:200]
        email = str(user.get("email") or "").strip()
        if email:
            return email[:200]
    display = str((message or {}).get("sender_display_name") or "Customer").strip()
    return display[:200] or "Customer"


def _is_technician(user: Mapping[str, Any] | None) -> bool:
    return bool(user and (user.get("is_super_admin") or user.get("is_helpdesk_technician")))


async def notify_new_chat(
    *,
    room: Mapping[str, Any],
    actor: Mapping[str, Any] | None = None,
) -> None:
    """Send an ntfy alert for a newly opened customer chat when enabled."""
    settings = get_settings()
    if not settings.ntfy_chat_new_enabled:
        return

    subject = _chat_subject(room)
    actor_name = _sender_name(actor)
    try:
        await modules_service.trigger_module(
            "ntfy",
            {
                "title": "New Chat",
                "message": f"{actor_name} started a new chat: {subject}",
                "priority": "default",
                "tags": "speech_balloon",
            },
            background=True,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to send new chat ntfy notification", room_id=room.get("id"), error=str(exc))


async def notify_chat_reply(
    *,
    room: Mapping[str, Any],
    message: Mapping[str, Any],
    actor: Mapping[str, Any] | None = None,
) -> None:
    """Send an ntfy alert for customer chat replies without reply content."""
    settings = get_settings()
    if not settings.ntfy_chat_reply_enabled or _is_technician(actor):
        return

    subject = _chat_subject(room)
    actor_name = _sender_name(actor, message)
    try:
        await modules_service.trigger_module(
            "ntfy",
            {
                "title": "Chat Reply",
                "message": f"{actor_name} replied to chat: {subject}",
                "priority": "default",
                "tags": "speech_balloon",
            },
            background=True,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to send chat reply ntfy notification", room_id=room.get("id"), error=str(exc))
