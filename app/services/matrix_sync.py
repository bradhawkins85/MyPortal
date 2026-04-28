from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.repositories import chat as chat_repo
from app.services import matrix as matrix_service

_settings = get_settings()
_running = False


async def process_sync_response(sync_data: dict[str, Any]) -> None:
    """Process a /sync response and insert new messages into the database."""
    rooms = sync_data.get("rooms", {})
    join_data = rooms.get("join", {})

    for matrix_room_id, room_data in join_data.items():
        room = await chat_repo.get_room_by_matrix_id(matrix_room_id)
        if not room:
            continue

        timeline = room_data.get("timeline", {})
        events = timeline.get("events", [])

        for event in events:
            event_type = event.get("type")
            if event_type not in ("m.room.message", "m.room.encrypted"):
                continue

            event_id = event.get("event_id")
            if not event_id:
                continue

            existing = await chat_repo.get_message_by_event_id(event_id)
            if existing:
                continue

            sender = event.get("sender", "")
            content = event.get("content", {})
            origin_server_ts = event.get("origin_server_ts", 0)
            sent_at = datetime.fromtimestamp(origin_server_ts / 1000, tz=timezone.utc).replace(tzinfo=None)

            if event_type == "m.room.encrypted":
                # The bot cannot decrypt E2EE messages; store a placeholder so
                # the room's timeline and last_message_at remain accurate.
                body = "[encrypted message]"
                msgtype = "m.room.encrypted"
            else:
                body = content.get("body", "")
                msgtype = content.get("msgtype", "m.text")

            link = await chat_repo.get_chat_user_link(matrix_user_id=sender)
            portal_user_id = link["user_id"] if link else None

            sender_display_name: str | None = None
            if portal_user_id:
                from app.repositories import users as user_repo
                user = await user_repo.get_user_by_id(portal_user_id)
                if user:
                    full_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
                    sender_display_name = full_name or user.get("email") or None
            if not sender_display_name:
                sender_display_name = await matrix_service.get_display_name(sender)

            await chat_repo.add_message(
                room_id=room["id"],
                matrix_event_id=event_id,
                sender_matrix_id=sender,
                body=body,
                msgtype=msgtype,
                sender_user_id=portal_user_id,
                sender_display_name=sender_display_name,
                sent_at=sent_at,
            )

            await chat_repo.update_room(room["id"], last_message_at=sent_at, updated_at=sent_at)

        member_events = [e for e in events if e.get("type") == "m.room.member"]
        for event in member_events:
            content = event.get("content", {})
            if content.get("membership") == "join":
                sender = event.get("state_key", "")
                invites = await chat_repo.list_invites(room["id"])
                for invite in invites:
                    if (
                        invite.get("provisioned_matrix_user_id") == sender
                        and invite["status"] in ("pending", "sent")
                    ):
                        await chat_repo.update_invite(invite["id"], status="accepted")


async def run_sync_loop() -> None:
    """Long-poll sync loop. Should be run as a background task."""
    global _running
    if _running:
        return
    _running = True

    try:
        next_batch = await chat_repo.get_sync_state()

        while _running:
            if not _settings.matrix_enabled or not _settings.matrix_bot_access_token:
                await asyncio.sleep(30)
                continue

            try:
                sync_data = await matrix_service.sync(since=next_batch, timeout_ms=30_000)
                next_batch = sync_data.get("next_batch")

                if next_batch:
                    await chat_repo.save_sync_state(next_batch)

                await process_sync_response(sync_data)

            except Exception as exc:
                log_error("Matrix sync error", error=str(exc))
                await asyncio.sleep(10)
    finally:
        _running = False


def stop_sync_loop() -> None:
    global _running
    _running = False
