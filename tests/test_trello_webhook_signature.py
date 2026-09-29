"""Trello webhook POSTs must carry a valid X-Trello-Webhook signature."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes import trello as trello_routes

CALLBACK_URL = "https://portal.example.com/api/integration-modules/trello/webhook"
SECRET = "trello-app-secret"
BODY = json.dumps({"action": {"type": "updateBoard", "data": {}}}).encode("utf-8")


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


async def _call(signature: str | None, secret: str = SECRET):
    with patch(
        "app.services.modules.get_module_settings",
        AsyncMock(return_value={"api_secret": secret}),
    ), patch.object(trello_routes.webhook_monitor, "log_incoming_webhook", AsyncMock()):
        return await trello_routes.trello_webhook_receive(_request(BODY, signature))


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
