from __future__ import annotations

from datetime import datetime

import pytest

from app.api.routes import tray as tray_routes


class _PopupRequest:
    headers = {"X-Tray-CSRF": "csrf-token"}

    async def json(self) -> dict[str, str]:
        return {"body": "I still need help"}


@pytest.mark.anyio("asyncio")
async def test_popup_chat_send_message_syncs_linked_ticket(monkeypatch):
    """Tray popup/client replies should mirror to the room's linked ticket."""

    room = {
        "id": 42,
        "status": "open",
        "matrix_room_id": "",
        "linked_ticket_id": 1001,
    }
    created_message = {
        "id": 501,
        "room_id": 42,
        "matrix_event_id": None,
        "sender_matrix_id": "@tray-device-7:tray",
        "sender_user_id": None,
        "sender_display_name": "CLIENT-PC",
        "body": "I still need help",
        "sent_at": datetime(2026, 6, 10, 12, 0, 0),
    }
    synced: dict[str, object] = {}
    room_updates: list[dict[str, object]] = []

    monkeypatch.setattr(
        tray_routes,
        "_parse_popup_session",
        lambda request: {
            "device_id": 7,
            "room_id": 42,
            "company_id": 3,
            "csrf": "csrf-token",
        },
    )

    async def fake_get_room(room_id: int) -> dict[str, object]:
        assert room_id == 42
        return room

    async def fake_get_device_by_id(device_id: int) -> dict[str, object]:
        assert device_id == 7
        return {"hostname": "CLIENT-PC"}

    async def fake_add_message(**kwargs):
        assert kwargs["room_id"] == 42
        assert kwargs["body"] == "I still need help"
        assert kwargs["sender_user_id"] is None
        assert kwargs["sender_display_name"] == "CLIENT-PC"
        assert kwargs["sent_at"] is not None
        return {**created_message, "sent_at": kwargs["sent_at"]}

    async def fake_update_room(room_id: int, **fields):
        room_updates.append({"room_id": room_id, **fields})

    async def fake_sync_chat_message_to_ticket(**kwargs):
        synced.update(kwargs)

    assistant_activity: list[tuple[int, object]] = []

    async def fake_handle_user_message(room_id: int, sent_at):
        assistant_activity.append((room_id, sent_at))

    monkeypatch.setattr(tray_routes.chat_repo, "get_room", fake_get_room)
    monkeypatch.setattr(tray_routes.tray_repo, "get_device_by_id", fake_get_device_by_id)
    monkeypatch.setattr(tray_routes.chat_repo, "add_message", fake_add_message)
    monkeypatch.setattr(tray_routes.chat_repo, "update_room", fake_update_room)
    monkeypatch.setattr(
        tray_routes.chat_ticket_sync,
        "sync_chat_message_to_ticket",
        fake_sync_chat_message_to_ticket,
    )
    monkeypatch.setattr(
        tray_routes.matrix_ai_waiting_assistant,
        "handle_user_message",
        fake_handle_user_message,
    )
    response = await tray_routes.popup_chat_send_message(_PopupRequest(), 42)

    assert response.status_code == 201
    assert synced["room"] == room
    assert synced["message"]["id"] == 501
    assert synced["message"]["body"] == "I still need help"
    assert synced["author_id"] is None
    assert room_updates
    assert room_updates[0]["room_id"] == 42
    assert room_updates[0]["last_message_at"] == room_updates[0]["updated_at"]
    assert assistant_activity == [(42, room_updates[0]["last_message_at"])]
