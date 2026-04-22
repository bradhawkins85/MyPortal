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
            if event.get("type") != "m.room.message":
                continue

            event_id = event.get("event_id")
            if not event_id:
                continue

            existing = await chat_repo.get_message_by_event_id(event_id)
            if existing:
                continue

            sender = event.get("sender", "")
            content = event.get("content", {})
            body = content.get("body", "")
            msgtype = content.get("msgtype", "m.text")
            origin_server_ts = event.get("origin_server_ts", 0)
            sent_at = datetime.fromtimestamp(origin_server_ts / 1000, tz=timezone.utc).replace(tzinfo=None)

            link = await chat_repo.get_chat_user_link(matrix_user_id=sender)
            portal_user_id = link["user_id"] if link else None

            await chat_repo.add_message(
                room_id=room["id"],
                matrix_event_id=event_id,
                sender_matrix_id=sender,
                body=body,
                msgtype=msgtype,
                sender_user_id=portal_user_id,
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

            except matrix_service.MatrixError as exc:
                if exc.errcode in ("M_UNKNOWN_TOKEN", "M_MISSING_TOKEN"):
                    log_error(
                        "Matrix access token is invalid or missing — "
                        "update MATRIX_BOT_ACCESS_TOKEN in .env and restart",
                        error=str(exc),
                    )
                    await asyncio.sleep(300)
                else:
                    log_error("Matrix sync error", error=str(exc))
                    await asyncio.sleep(10)
            except Exception as exc:
                log_error("Matrix sync error", error=str(exc))
                await asyncio.sleep(10)
    finally:
        _running = False


def stop_sync_loop() -> None:
    global _running
    _running = False
