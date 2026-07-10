from __future__ import annotations

from unittest.mock import AsyncMock

import asyncio

from app.api.routes import trello as trello_routes


def test_build_trello_ticket_description_includes_link_and_escaped_content():
    description = trello_routes._build_trello_ticket_description(
        "First line\n<script>alert('x')</script>",
        "https://trello.com/c/abc123/card?x=<bad>",
    )

    assert description is not None
    assert '<strong>Trello card:</strong>' in description
    assert 'href="https://trello.com/c/abc123/card?x=&lt;bad&gt;"' in description
    assert 'target="_blank" rel="noopener noreferrer"' in description
    assert '<strong>Trello card content:</strong>' in description
    assert "First line<br>&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in description
    assert "<script>" not in description


def test_build_trello_ticket_description_returns_none_without_link_or_content():
    assert trello_routes._build_trello_ticket_description(None, None) is None
    assert trello_routes._build_trello_ticket_description("  ", "  ") is None


def test_handle_create_card_fetches_full_card_and_creates_ticket_with_link(monkeypatch):
    async def run_test():
        created_payload: dict = {}

        monkeypatch.setattr(trello_routes.trello_service, "find_ticket_for_card", AsyncMock(return_value=None))
        monkeypatch.setattr(
            trello_routes.trello_service,
            "get_company_for_board",
            AsyncMock(return_value={"id": 42, "trello_api_key": "key", "trello_token": "token"}),
        )
        monkeypatch.setattr(
            trello_routes.trello_service,
            "get_card",
            AsyncMock(
                return_value={
                    "name": "Full card title",
                    "desc": "Full card body",
                    "url": "https://trello.com/c/full-card",
                }
            ),
        )
        monkeypatch.setattr(trello_routes.trello_service, "post_ticket_created_comment", AsyncMock())

        async def fake_create_ticket(**kwargs):
            created_payload.update(kwargs)
            return {"id": 123, "ticket_number": "T-123"}

        monkeypatch.setattr(trello_routes.tickets_service, "create_ticket", fake_create_ticket)

        await trello_routes._handle_create_card(
            "board-1",
            "card-1",
            {"name": "Webhook title", "desc": "Webhook body", "shortUrl": "https://trello.com/c/short"},
            {},
        )

        assert created_payload["subject"] == "Full card title"
        assert created_payload["company_id"] == 42
        assert created_payload["external_reference"] == "trello:card-1"
        assert "https://trello.com/c/full-card" in created_payload["description"]
        assert "Full card body" in created_payload["description"]

    asyncio.run(run_test())


def test_trello_external_reference_helpers():
    from app.services import trello as trello_service

    assert trello_service.card_external_reference("card-1") == "trello:card-1"
    assert trello_service.card_external_reference("trello:card-1") == "trello:card-1"
    assert trello_service.card_id_from_external_reference("trello:card-1") == "card-1"
    assert trello_service.card_id_from_external_reference("card-1") == "card-1"
    assert trello_service.card_id_from_external_reference("") is None
