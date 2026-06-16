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
