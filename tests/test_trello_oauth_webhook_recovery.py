"""Regression tests: OAuth 2.0 Trello webhooks that Trello disabled.

Trello cannot list OAuth 2.0 webhooks, and a 400 "already exists" used to be
reported as a working webhook even when Trello had disabled it, so card
events silently stopped arriving.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from app.api.routes import trello as trello_routes
from app.services import trello as trello_service

ROOT = Path(__file__).resolve().parents[1]
BOARD_ID = "68808781a72f35d7fbcc995c"
HOOK_ID = "0123456789abcdef01234567"
CALLBACK = "https://portal.example.com/api/integration-modules/trello/webhook"
OAUTH = trello_service.TrelloAuth(mode="oauth", headers={"Authorization": "Bearer t"})


@pytest.fixture
def trello_api(monkeypatch):
    """Route Trello HTTP calls to a handler and record every request."""
    calls: list[httpx.Request] = []
    state: dict = {"handler": None}

    def _transport_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return state["handler"](request)

    def _client(_cls, *args, **kwargs):
        kwargs.pop("timeout", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(_transport_handler))

    monkeypatch.setattr(trello_service, "monitored_client", _client)
    monkeypatch.setattr(trello_service, "_get_module_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(trello_service, "get_auth_for_company", AsyncMock(return_value=OAUTH))
    monkeypatch.setattr(trello_service, "_remove_legacy_webhooks", AsyncMock())
    store = AsyncMock()
    monkeypatch.setattr(trello_service.company_repo, "set_trello_webhook_id", store)
    state["calls"] = calls
    state["store"] = store
    return state


@pytest.mark.asyncio
async def test_disabled_stored_webhook_is_reactivated(trello_api):
    hook = {"id": HOOK_ID, "idModel": BOARD_ID, "callbackURL": CALLBACK}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={**hook, "active": False})
        if request.method == "PUT":
            assert json.loads(request.content) == {"active": True}
            return httpx.Response(200, json={**hook, "active": True})
        raise AssertionError(f"unexpected {request.method}")

    trello_api["handler"] = handler
    company = {"id": 7, "trello_webhook_id": HOOK_ID}

    result = await trello_service.register_webhook(BOARD_ID, CALLBACK, company=company)

    assert result["active"] is True
    assert [c.method for c in trello_api["calls"]] == ["GET", "PUT"]
    assert all(c.headers["authorization"] == "Bearer t" for c in trello_api["calls"])


@pytest.mark.asyncio
async def test_active_stored_webhook_is_reused_without_posting(trello_api):
    hook = {"id": HOOK_ID, "idModel": BOARD_ID, "callbackURL": CALLBACK, "active": True}
    trello_api["handler"] = lambda request: httpx.Response(200, json=hook)

    result = await trello_service.register_webhook(
        BOARD_ID, CALLBACK, company={"id": 7, "trello_webhook_id": HOOK_ID}
    )

    assert result == hook
    assert [c.method for c in trello_api["calls"]] == ["GET"]


@pytest.mark.asyncio
async def test_deleted_stored_webhook_is_recreated_and_id_saved(trello_api):
    new_id = "fedcba9876543210fedcba98"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={"id": new_id, "idModel": BOARD_ID})

    trello_api["handler"] = handler
    company = {"id": 7, "trello_webhook_id": HOOK_ID}

    result = await trello_service.register_webhook(BOARD_ID, CALLBACK, company=company)

    assert result["id"] == new_id
    assert [c.method for c in trello_api["calls"]] == ["GET", "POST"]
    trello_api["store"].assert_any_await(7, None)
    trello_api["store"].assert_awaited_with(7, new_id)


@pytest.mark.asyncio
async def test_new_webhook_id_is_saved(trello_api):
    trello_api["handler"] = lambda request: httpx.Response(
        200, json={"id": HOOK_ID, "idModel": BOARD_ID}
    )

    await trello_service.register_webhook(BOARD_ID, CALLBACK, company={"id": 7})

    trello_api["store"].assert_awaited_once_with(7, HOOK_ID)


@pytest.mark.asyncio
async def test_unknown_existing_oauth_webhook_is_not_reported_as_working(trello_api):
    trello_api["handler"] = lambda request: httpx.Response(
        400, text="A webhook with that callback, model, and token already exists"
    )

    with pytest.raises(trello_service.TrelloWebhookConflictError):
        await trello_service.register_webhook(BOARD_ID, CALLBACK, company={"id": 7})


@pytest.mark.asyncio
async def test_register_route_returns_conflict_with_guidance(monkeypatch):
    monkeypatch.setattr(
        "app.services.module_gate.require_enabled", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        trello_routes.trello_service,
        "get_company_for_board",
        AsyncMock(return_value={"id": 7}),
    )
    monkeypatch.setattr(
        trello_routes.trello_service,
        "register_webhook",
        AsyncMock(side_effect=trello_service.TrelloWebhookConflictError("reconnect")),
    )
    monkeypatch.setattr(trello_routes, "_build_public_callback_url", lambda request: CALLBACK)

    with pytest.raises(HTTPException) as excinfo:
        await trello_routes.register_trello_webhook(BOARD_ID, request=object(), _current_user={})

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "reconnect"


def test_migration_adds_webhook_id_column_idempotently():
    sql = (ROOT / "migrations/561_trello_webhook_id.sql").read_text()
    assert "-- phase: expand" in sql
    assert "ADD COLUMN IF NOT EXISTS trello_webhook_id" in sql
