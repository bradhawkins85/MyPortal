"""Trello webhook POSTs must carry a valid X-Trello-Webhook signature."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes import trello as trello_routes

CALLBACK_URL = "https://portal.example.com/api/integration-modules/trello/webhook"
SECRET = "trello-app-secret"
BODY = json.dumps({"action": {"type": "updateBoard", "data": {}}}).encode("utf-8")
BOARD_BODY = json.dumps(
    {"action": {"type": "createCard", "data": {"board": {"id": "board-123"}}}}
).encode("utf-8")


def _request(body: bytes, signature: str | None) -> Request:
    headers = [(b"host", b"portal.example.com"), (b"content-type", b"application/json")]
    if signature is not None:
        headers.append((b"x-trello-webhook", signature.encode("ascii")))
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "https",
        "path": "/api/integration-modules/trello/webhook",
        "raw_path": b"/api/integration-modules/trello/webhook",
        "query_string": b"",
        "headers": headers,
        "server": ("portal.example.com", 443),
        "client": ("203.0.113.5", 1234),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _settings(secret: str = SECRET) -> SimpleNamespace:
    return SimpleNamespace(
        trello_webhook_secret=secret,
        trello_api_secret="",
        public_base_url="https://portal.example.com",
        portal_url="https://portal.example.com",
    )


async def _call(signature: str | None, secret: str = SECRET, body: bytes = BODY):
    with patch(
        "app.api.routes.trello.get_settings",
        return_value=_settings(secret),
    ), patch(
        "app.services.modules.get_module_settings",
        AsyncMock(return_value={}),
    ), patch.object(trello_routes.webhook_monitor, "log_incoming_webhook", AsyncMock()):
        return await trello_routes.trello_webhook_receive(_request(body, signature))


@pytest.mark.anyio
async def test_valid_signature_is_accepted():
    signature = trello_routes.compute_trello_signature(SECRET, BODY, CALLBACK_URL)
    response = await _call(signature)
    assert response.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("signature", [None, "", "bm90LWEtc2lnbmF0dXJl"])
async def test_missing_or_invalid_signature_is_rejected(signature):
    with pytest.raises(HTTPException) as exc_info:
        await _call(signature)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_rejected_when_no_secret_configured():
    signature = trello_routes.compute_trello_signature("", BODY, CALLBACK_URL)
    with pytest.raises(HTTPException) as exc_info:
        await _call(signature, secret="")
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_board_specific_api_key_secret_map_is_used():
    company = {"id": 7, "trello_api_key": "company-key"}
    module_settings = {
        "api_secret": {"by_api_key": {"company-key": "company-secret"}},
    }
    signature = trello_routes.compute_trello_signature(
        "company-secret", BOARD_BODY, CALLBACK_URL
    )
    with patch(
        "app.api.routes.trello.get_settings",
        return_value=_settings(""),
    ), patch(
        "app.services.modules.get_module_settings",
        AsyncMock(return_value=module_settings),
    ), patch(
        "app.services.trello.get_company_for_board",
        AsyncMock(return_value=company),
    ), patch.object(trello_routes.webhook_monitor, "log_incoming_webhook", AsyncMock()):
        response = await trello_routes.trello_webhook_receive(
            _request(BOARD_BODY, signature)
        )
    assert response.status_code == 200
