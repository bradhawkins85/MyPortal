from __future__ import annotations

from datetime import datetime

import pytest

from app.services import chat_ticket_sync


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_ticket_reply_to_chat_omits_billable_time(monkeypatch):
    sent_payloads: list[dict] = []
    added_messages: list[dict] = []

    async def fake_get_link_by_ticket_reply_id(reply_id: int):
        return None

    async def fake_get_room_by_ticket_id(ticket_id: int):
        return {
            "id": 5,
            "matrix_room_id": "!room:example.test",
            "status": "open",
        }

    async def fake_get_user_by_id(user_id: int):
        return {"id": user_id, "first_name": "Tech", "last_name": "User", "email": "tech@example.test"}

    async def fake_send_message(room_id: str, body: str, **kwargs):
        sent_payloads.append({"room_id": room_id, "body": body, **kwargs})
        return {"event_id": "$ticketreply"}

    async def fake_add_message(**kwargs):
        added_messages.append(kwargs)
        return {"id": 42, **kwargs}

    async def fake_update_room(*args, **kwargs):
        return None

    async def fake_link_chat_ticket(**kwargs):
        return None

    async def fake_broadcast_refresh(*args, **kwargs):
        return None

    monkeypatch.setattr(chat_ticket_sync, "get_link_by_ticket_reply_id", fake_get_link_by_ticket_reply_id)
    monkeypatch.setattr(chat_ticket_sync.chat_repo, "get_room_by_ticket_id", fake_get_room_by_ticket_id)
    monkeypatch.setattr(chat_ticket_sync.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(chat_ticket_sync.matrix_service, "send_message", fake_send_message)
    monkeypatch.setattr(chat_ticket_sync.chat_repo, "add_message", fake_add_message)
    monkeypatch.setattr(chat_ticket_sync.chat_repo, "update_room", fake_update_room)
    monkeypatch.setattr(chat_ticket_sync, "link_chat_ticket", fake_link_chat_ticket)
    monkeypatch.setattr(chat_ticket_sync.refresh_notifier, "broadcast_refresh", fake_broadcast_refresh)

    await chat_ticket_sync.sync_ticket_reply_to_chat(
        ticket_id=10,
        reply={
            "id": 99,
            "author_id": 7,
            "body": "<p>Public update only.</p>",
            "is_internal": False,
            "minutes_spent": 60,
            "is_billable": True,
            "created_at": datetime(2026, 1, 1, 12, 0, 0),
        },
    )

    assert sent_payloads
    assert sent_payloads[0]["body"] == "Public update only."
    assert "60" not in sent_payloads[0]["body"]
    assert "billable" not in sent_payloads[0]["body"].lower()
    assert added_messages[0]["body"] == "Public update only."


@pytest.mark.anyio
async def test_create_ticket_from_chat_allows_company_only_when_requester_unknown(monkeypatch):
    created_payloads: list[dict] = []
    room = {
        "id": 12,
        "subject": "Chat from PC-01",
        "company_id": 34,
        "created_by_user_id": None,
        "assigned_tech_user_id": 8,
        "linked_ticket_id": None,
    }

    async def fake_get_room(room_id: int):
        return room

    async def fake_get_messages(room_id: int, limit: int = 50):
        return [
            {
                "sender_display_name": "Customer",
                "sent_at": datetime(2026, 1, 1, 12, 0, 0),
                "body": "Need help",
            }
        ]

    async def fake_resolve_status_or_default(status):
        return "open"

    async def fake_create_ticket(**kwargs):
        created_payloads.append(kwargs)
        return {"id": 77, **kwargs}

    async def fake_update_room(*args, **kwargs):
        return None

    async def fake_link_chat_ticket(**kwargs):
        return None

    async def fake_emit_ticket_updated_event(*args, **kwargs):
        return None

    monkeypatch.setattr(chat_ticket_sync.chat_repo, "get_room", fake_get_room)
    monkeypatch.setattr(chat_ticket_sync.chat_repo, "get_messages", fake_get_messages)
    monkeypatch.setattr(chat_ticket_sync.tickets_service, "resolve_status_or_default", fake_resolve_status_or_default)
    monkeypatch.setattr(chat_ticket_sync.tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(chat_ticket_sync.chat_repo, "update_room", fake_update_room)
    monkeypatch.setattr(chat_ticket_sync, "link_chat_ticket", fake_link_chat_ticket)
    monkeypatch.setattr(chat_ticket_sync.tickets_service, "emit_ticket_updated_event", fake_emit_ticket_updated_event)

    ticket = await chat_ticket_sync.create_ticket_from_chat(12, actor={"id": 8})

    assert ticket["id"] == 77
    assert created_payloads[0]["requester_id"] is None
    assert created_payloads[0]["company_id"] == 34
    assert "Need help" in created_payloads[0]["description"]
