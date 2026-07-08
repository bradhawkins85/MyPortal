from __future__ import annotations

from datetime import datetime

import pytest

from app.services import tray_chat_notifications


@pytest.mark.anyio("asyncio")
async def test_notify_tray_device_of_chat_message_sends_incoming_technician_reply(monkeypatch):
    sent_payloads: list[tuple[str, dict[str, object]]] = []
    logged: list[dict[str, object]] = []

    async def fake_get_device_by_id(device_id: int) -> dict[str, object]:
        assert device_id == 7
        return {"id": 7, "device_uid": "device-uid-7"}

    async def fake_send_to_device(device_uid: str, payload: dict[str, object]) -> bool:
        sent_payloads.append((device_uid, payload))
        return True

    async def fake_log_command(**kwargs):
        logged.append(kwargs)
        return 1

    monkeypatch.setattr(tray_chat_notifications.tray_repo, "get_device_by_id", fake_get_device_by_id)
    monkeypatch.setattr(tray_chat_notifications.tray_service, "send_to_device", fake_send_to_device)
    monkeypatch.setattr(tray_chat_notifications.tray_repo, "log_command", fake_log_command)

    delivered = await tray_chat_notifications.notify_tray_device_of_chat_message(
        room={
            "id": 42,
            "tray_device_id": 7,
            "matrix_room_id": "!room:example",
            "subject": "Printer issue",
        },
        message={
            "id": 501,
            "matrix_event_id": "$event",
            "sender_matrix_id": "@tech:example",
            "sender_display_name": "Alex Tech",
            "body": "Please try printing again.",
            "sent_at": datetime(2026, 6, 10, 12, 0, 0),
        },
    )

    assert delivered is True
    assert sent_payloads == [
        (
            "device-uid-7",
            {
                "type": "chat_message",
                "room_id": 42,
                "matrix_room_id": "!room:example",
                "subject": "Printer issue",
                "sender": "Alex Tech",
                "message": "Please try printing again.",
                "message_id": 501,
                "matrix_event_id": "$event",
                "sent_at": "2026-06-10T12:00:00",
            },
        )
    ]
    assert logged[0]["command"] == "chat_message"
    assert logged[0]["status"] == "delivered"


@pytest.mark.anyio("asyncio")
async def test_notify_tray_device_of_chat_message_skips_closed_room(monkeypatch):
    async def fail_get_device_by_id(device_id: int):  # pragma: no cover - should not be called
        raise AssertionError("closed room messages should not resolve devices")

    monkeypatch.setattr(tray_chat_notifications.tray_repo, "get_device_by_id", fail_get_device_by_id)

    delivered = await tray_chat_notifications.notify_tray_device_of_chat_message(
        room={"id": 42, "tray_device_id": 7, "status": "closed"},
        message={"sender_matrix_id": "@bot:example", "body": "This chat has been closed."},
    )

    assert delivered is False


@pytest.mark.anyio("asyncio")
async def test_notify_tray_device_of_chat_message_skips_own_tray_message(monkeypatch):
    async def fail_get_device_by_id(device_id: int):  # pragma: no cover - should not be called
        raise AssertionError("own tray messages should not resolve devices")

    monkeypatch.setattr(tray_chat_notifications.tray_repo, "get_device_by_id", fail_get_device_by_id)

    delivered = await tray_chat_notifications.notify_tray_device_of_chat_message(
        room={"id": 42, "tray_device_id": 7},
        message={"sender_matrix_id": "@tray-device-7:tray", "body": "Client reply"},
    )

    assert delivered is False
