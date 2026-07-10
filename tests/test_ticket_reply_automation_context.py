from typing import Any

import pytest

from app.services import tickets as tickets_service


@pytest.mark.anyio
async def test_emit_ticket_replied_event_includes_internal_reply_context(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_get_ticket(ticket_id: int):
        assert ticket_id == 123
        return {"id": 123, "status": "open", "subject": "Example"}

    async def fake_enrich(ticket):
        return dict(ticket)

    async def fake_handle_event(event_name, context):
        captured["event_name"] = event_name
        captured["context"] = context
        return []

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service, "_enrich_ticket_context", fake_enrich)
    monkeypatch.setattr(tickets_service.automations_service, "handle_event", fake_handle_event)

    await tickets_service.emit_ticket_replied_event(
        123,
        actor_type="technician",
        actor={"id": 7, "email": "tech@example.test"},
        reply={"id": 55, "is_internal": 1, "body": "hidden"},
    )

    assert captured["event_name"] == "tickets.replied"
    assert captured["context"]["reply"]["id"] == 55
    assert captured["context"]["reply"]["is_internal"] is True
    assert captured["context"]["reply"]["kind"] == "internal_note"


@pytest.mark.anyio
async def test_emit_ticket_updated_event_includes_public_reply_context(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_get_ticket(ticket_id: int):
        assert ticket_id == 456
        return {"id": 456, "status": "open", "subject": "Example"}

    async def fake_enrich(ticket):
        return dict(ticket)

    async def fake_handle_event(event_name, context):
        captured["event_name"] = event_name
        captured["context"] = context
        return []

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service, "_enrich_ticket_context", fake_enrich)
    monkeypatch.setattr(tickets_service.automations_service, "handle_event", fake_handle_event)

    await tickets_service.emit_ticket_updated_event(
        456,
        actor_type="technician",
        actor={"id": 7, "email": "tech@example.test"},
        reply={"id": 77, "is_internal": False, "body": "public"},
    )

    assert captured["event_name"] == "tickets.updated"
    assert captured["context"]["ticket_update"]["actor_type"] == "technician"
    assert captured["context"]["reply"]["id"] == 77
    assert captured["context"]["reply"]["is_internal"] is False
    assert captured["context"]["reply"]["kind"] == "message"


def test_reply_message_filter_matches_updated_event_context():
    from app.services import automations as automations_service

    context = {
        "ticket_update": {"actor_type": "technician"},
        "reply": {"kind": "message", "is_internal": False},
    }
    filters = {
        "all": [
            {"match": {"ticket_update.actor_type": "technician"}},
            {"match": {"reply.kind": "message"}},
            {"match": {"reply.is_internal": False}},
        ]
    }

    assert automations_service._filters_match(filters, context)
