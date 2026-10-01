import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

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


def _trello_signature(secret: str, body: bytes, callback_url: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        body + callback_url.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


@pytest.mark.anyio
async def test_webhook_rejects_unsigned_comment_card(monkeypatch):
    monkeypatch.setattr(
        trello_routes,
        "get_settings",
        lambda: SimpleNamespace(
            public_base_url="https://portal.example.com",
            trello_webhook_secret="trello-app-secret",
        ),
    )
    create_reply_called = False

    async def fake_create_reply(**kwargs):
        nonlocal create_reply_called
        create_reply_called = True
        return {"id": 99}

    from app.repositories import tickets as tickets_repo

    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(
        trello_routes.webhook_monitor, "log_incoming_webhook", AsyncMock()
    )

    app = FastAPI()
    app.include_router(trello_routes.router)
    payload = {
        "action": {
            "type": "commentCard",
            "data": {
                "board": {"id": "board-123"},
                "card": {"id": "card-123"},
                "text": "Forged reply",
            },
            "memberCreator": {"fullName": "Mallory"},
        }
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://portal.example.com"
    ) as client:
        response = await client.post(
            "/api/integration-modules/trello/webhook", json=payload
        )

    assert response.status_code == 401
    assert create_reply_called is False


@pytest.mark.anyio
async def test_webhook_accepts_signed_comment_card(monkeypatch):
    secret = "trello-app-secret"
    callback_url = "https://portal.example.com/api/integration-modules/trello/webhook"
    monkeypatch.setattr(
        trello_routes,
        "get_settings",
        lambda: SimpleNamespace(
            public_base_url="https://portal.example.com",
            trello_webhook_secret=secret,
        ),
    )
    monkeypatch.setattr(
        trello_routes.trello_service,
        "find_ticket_for_card",
        AsyncMock(return_value={"id": 42, "external_reference": "trello:card-123"}),
    )
    monkeypatch.setattr(
        trello_routes.tickets_service, "emit_ticket_updated_event", AsyncMock()
    )
    monkeypatch.setattr(
        trello_routes.tickets_service, "emit_ticket_replied_event", AsyncMock()
    )
    monkeypatch.setattr(
        trello_routes.webhook_monitor, "log_incoming_webhook", AsyncMock()
    )

    from app.repositories import tickets as tickets_repo

    monkeypatch.setattr(
        tickets_repo, "create_reply", AsyncMock(return_value={"id": 99})
    )

    app = FastAPI()
    app.include_router(trello_routes.router)
    payload = {
        "action": {
            "type": "commentCard",
            "data": {
                "board": {"id": "board-123"},
                "card": {"id": "card-123"},
                "text": "Real reply",
            },
            "memberCreator": {"fullName": "Trello User"},
        }
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://portal.example.com"
    ) as client:
        response = await client.post(
            "/api/integration-modules/trello/webhook",
            content=body,
            headers={
                "content-type": "application/json",
                "x-trello-webhook": _trello_signature(secret, body, callback_url),
            },
        )

    assert response.status_code == 200
    tickets_repo.create_reply.assert_awaited_once()
    trello_routes.tickets_service.emit_ticket_replied_event.assert_awaited_once()
