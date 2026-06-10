from __future__ import annotations

import re
from datetime import datetime, timezone
from html import escape, unescape
from typing import Any, Mapping

from app.core.logging import log_error, log_info
from app.repositories import chat as chat_repo
from app.repositories import tickets as tickets_repo
from app.repositories import users as user_repo
from app.services import matrix as matrix_service
from app.services import tray_chat_notifications
from app.services import tickets as tickets_service
from app.services.realtime import refresh_notifier
from app.services.sanitization import sanitize_rich_text

_TAG_RE = re.compile(r"<[^>]+>")


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _html_to_text(value: str | None) -> str:
    text = _TAG_RE.sub(" ", str(value or ""))
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _display_name(user: Mapping[str, Any] | None, fallback: str = "Support") -> str:
    if not user:
        return fallback
    full_name = " ".join(
        part for part in (str(user.get("first_name") or "").strip(), str(user.get("last_name") or "").strip()) if part
    )
    return full_name or str(user.get("display_name") or user.get("email") or fallback)


async def create_ticket_from_chat(room_id: int, *, actor: Mapping[str, Any]) -> dict[str, Any]:
    """Create and link a helpdesk ticket from a chat room transcript."""

    room = await chat_repo.get_room(room_id)
    if not room:
        raise ValueError("Chat room not found")
    linked_ticket_id = room.get("linked_ticket_id")
    if linked_ticket_id:
        existing = await tickets_repo.get_ticket(int(linked_ticket_id))
        if existing:
            return existing

    requester_id: int | None = None
    if room.get("created_by_user_id"):
        try:
            requester_id = int(room["created_by_user_id"])
        except (TypeError, ValueError):
            requester_id = None

    messages = await chat_repo.get_messages(room_id, limit=1000)
    transcript_lines: list[str] = [
        f"Ticket created from chat #{room_id}.",
        "",
        "Chat transcript:",
    ]
    for message in messages:
        sender = str(message.get("sender_display_name") or message.get("sender_matrix_id") or "Unknown")
        sent_at = message.get("sent_at")
        timestamp = sent_at.isoformat() if hasattr(sent_at, "isoformat") else str(sent_at or "")
        body = _html_to_text(str(message.get("body") or ""))
        transcript_lines.append(f"[{timestamp} UTC] {sender}: {body}")

    status_value = await tickets_service.resolve_status_or_default(None)
    ticket = await tickets_service.create_ticket(
        subject=f"Chat: {room.get('subject') or f'Room {room_id}'}"[:255],
        description="\n".join(transcript_lines),
        requester_id=requester_id,
        company_id=room.get("company_id"),
        assigned_user_id=room.get("assigned_tech_user_id"),
        priority="normal",
        status=status_value,
        category="chat",
        module_slug="chat",
        external_reference=f"chat:{room_id}",
        trigger_automations=True,
        initial_reply_author_id=requester_id,
    )
    ticket_id = int(ticket["id"])
    await chat_repo.update_room(room_id, linked_ticket_id=ticket_id, updated_at=_utcnow_naive())

    await link_chat_ticket(room_id=room_id, ticket_id=ticket_id, sync_direction="ticket_created")
    await tickets_service.emit_ticket_updated_event(ticket_id, actor_type="technician", actor=actor)
    log_info("Created ticket from chat", room_id=room_id, ticket_id=ticket_id)
    return ticket


async def sync_chat_message_to_ticket(
    *,
    room: Mapping[str, Any],
    message: Mapping[str, Any],
    author_id: int | None = None,
) -> None:
    """Mirror a chat message into the linked ticket as a public reply."""

    if room.get("status") != "open" or not room.get("linked_ticket_id"):
        return
    chat_message_id = message.get("id")
    if isinstance(chat_message_id, int) and await get_link_by_chat_message_id(chat_message_id):
        return

    body = str(message.get("body") or "").strip()
    if not body:
        return
    sanitized = sanitize_rich_text(body)
    if not sanitized.has_rich_content:
        return

    ticket_id = int(room["linked_ticket_id"])
    reply = await tickets_repo.create_reply(
        ticket_id=ticket_id,
        author_id=author_id if author_id is not None else message.get("sender_user_id"),
        body=sanitized.html,
        is_internal=False,
        external_reference=f"chat:{chat_message_id or message.get('matrix_event_id') or ''}",
    )
    if isinstance(chat_message_id, int) and isinstance(reply.get("id"), int):
        await link_chat_ticket(
            room_id=int(room["id"]),
            ticket_id=ticket_id,
            chat_message_id=chat_message_id,
            ticket_reply_id=int(reply["id"]),
            sync_direction="chat_to_ticket",
        )
    try:
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
    except RuntimeError:
        pass
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    await tickets_service.broadcast_ticket_event(action="reply", ticket_id=ticket_id)
    await tickets_service.emit_ticket_updated_event(ticket_id, actor_type="requester")


async def sync_ticket_reply_to_chat(*, ticket_id: int, reply: Mapping[str, Any]) -> None:
    """Mirror a public ticket reply into the linked open chat room."""

    if reply.get("is_internal"):
        return
    reply_id = reply.get("id")
    if isinstance(reply_id, int) and await get_link_by_ticket_reply_id(reply_id):
        return
    external_reference = str(reply.get("external_reference") or "")
    if external_reference.startswith("chat:"):
        return

    room = await chat_repo.get_room_by_ticket_id(ticket_id)
    if not room or room.get("status") != "open":
        return

    body_text = _html_to_text(str(reply.get("body") or ""))
    if not body_text:
        return

    author = None
    if reply.get("author_id"):
        author = await user_repo.get_user_by_id(int(reply["author_id"]))
    sender_display_name = _display_name(author)
    matrix_event_id: str | None = None
    try:
        formatted_reply = sanitize_rich_text(str(reply.get("body") or "")).html
        matrix_resp = await matrix_service.send_message(
            str(room["matrix_room_id"]),
            body_text,
            formatted_body=f"<strong>{escape(sender_display_name)}</strong>: {formatted_reply}",
        )
        matrix_event_id = matrix_resp.get("event_id")
    except Exception as exc:
        log_error("Failed to mirror ticket reply to Matrix chat", ticket_id=ticket_id, error=str(exc))
        return

    sent_at = reply.get("created_at") if isinstance(reply.get("created_at"), datetime) else _utcnow_naive()
    if sent_at.tzinfo is not None:
        sent_at = sent_at.astimezone(timezone.utc).replace(tzinfo=None)
    chat_message = await chat_repo.add_message(
        room_id=int(room["id"]),
        matrix_event_id=matrix_event_id,
        sender_matrix_id="ticket",
        sender_user_id=reply.get("author_id"),
        sender_display_name=sender_display_name,
        body=body_text,
        sent_at=sent_at,
    )
    await chat_repo.update_room(int(room["id"]), last_message_at=sent_at, updated_at=sent_at)
    if isinstance(reply_id, int) and isinstance(chat_message.get("id"), int):
        await link_chat_ticket(
            room_id=int(room["id"]),
            ticket_id=ticket_id,
            chat_message_id=int(chat_message["id"]),
            ticket_reply_id=reply_id,
            sync_direction="ticket_to_chat",
        )
    message_payload = dict(chat_message)
    for key, value in list(message_payload.items()):
        if isinstance(value, datetime):
            message_payload[key] = value.isoformat()
    await refresh_notifier.broadcast_refresh(
        topics=[f"chat:room:{room['id']}"],
        data={"message": message_payload, "room_id": room["id"]},
    )
    await tray_chat_notifications.notify_tray_device_of_chat_message(
        room=room,
        message=message_payload,
    )


async def get_link_by_chat_message_id(chat_message_id: int) -> dict[str, Any] | None:
    from app.core.database import db

    row = await db.fetch_one(
        "SELECT * FROM chat_ticket_reply_links WHERE chat_message_id = %s",
        (chat_message_id,),
    )
    return dict(row) if row else None


async def get_link_by_ticket_reply_id(ticket_reply_id: int) -> dict[str, Any] | None:
    from app.core.database import db

    row = await db.fetch_one(
        "SELECT * FROM chat_ticket_reply_links WHERE ticket_reply_id = %s",
        (ticket_reply_id,),
    )
    return dict(row) if row else None


async def link_chat_ticket(
    *,
    room_id: int,
    ticket_id: int,
    sync_direction: str,
    chat_message_id: int | None = None,
    ticket_reply_id: int | None = None,
) -> None:
    from app.core.database import db

    if db.is_sqlite():
        await db.execute(
            """
            INSERT OR IGNORE INTO chat_ticket_reply_links
                (room_id, ticket_id, chat_message_id, ticket_reply_id, sync_direction, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (room_id, ticket_id, chat_message_id, ticket_reply_id, sync_direction, _utcnow_naive()),
        )
    else:
        await db.execute(
            """
            INSERT IGNORE INTO chat_ticket_reply_links
                (room_id, ticket_id, chat_message_id, ticket_reply_id, sync_direction, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (room_id, ticket_id, chat_message_id, ticket_reply_id, sync_direction, _utcnow_naive()),
        )
