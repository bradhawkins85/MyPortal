from typing import Any

import pytest

from app.api.routes import trello as trello_routes


@pytest.mark.anyio
async def test_comment_card_emits_updated_and_replied_automation_events(monkeypatch):
    ticket = {
        "id": 42,
        "status": "open",
        "external_reference": "trello:card-123",
    }
    reply = {"id": 99, "is_internal": False, "body": "from Trello"}
    emitted: list[tuple[str, int, dict[str, Any]]] = []

    async def fake_find_ticket_for_card(card_id: str):
        assert card_id == "card-123"
        return ticket

    async def fake_create_reply(**kwargs):
        assert kwargs["ticket_id"] == 42
        assert kwargs["author_id"] is None
        assert kwargs["is_internal"] is False
        assert "Client Name (Trello)" in kwargs["body"]
        assert "Looks good" in kwargs["body"]
        return reply

    async def fake_emit_updated(ticket_id: int, **kwargs):
        emitted.append(("updated", ticket_id, kwargs))

    async def fake_emit_replied(ticket_id: int, **kwargs):
        emitted.append(("replied", ticket_id, kwargs))

    monkeypatch.setattr(
        trello_routes.trello_service, "find_ticket_for_card", fake_find_ticket_for_card
    )
    monkeypatch.setattr(
        trello_routes.tickets_service, "emit_ticket_updated_event", fake_emit_updated
    )
    monkeypatch.setattr(
        trello_routes.tickets_service, "emit_ticket_replied_event", fake_emit_replied
    )

    from app.repositories import tickets as tickets_repo

    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)

    await trello_routes._handle_comment_card(
        "card-123",
        {"text": "Looks good"},
        {"memberCreator": {"fullName": "Client Name"}},
    )

    assert emitted == [
        ("updated", 42, {"actor_type": "system", "reply": reply}),
        ("replied", 42, {"actor_type": "system", "reply": reply}),
    ]
