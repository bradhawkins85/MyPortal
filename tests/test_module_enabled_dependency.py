from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.dependencies.modules import require_module_enabled
from app.features.calls.routes import router as calls_router
from app.repositories import calls as calls_repo
from app.services import modules as modules_service


def test_enabled_module_dependency_returns_unredacted_module(monkeypatch):
    expected = {"slug": "trello", "enabled": True, "settings": {"secret": "visible"}}
    calls = []

    async def fake_get_module(slug: str, *, redact: bool = True):
        calls.append((slug, redact))
        return expected

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    assert asyncio.run(require_module_enabled("trello")()) is expected
    assert calls == [("trello", False)]


@pytest.mark.parametrize("module", [None, {"slug": "trello", "enabled": False}])
def test_missing_and_disabled_modules_share_documented_503(monkeypatch, module):
    async def fake_get_module(slug: str, *, redact: bool = True):
        return module

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(require_module_enabled("trello")())

    assert raised.value.status_code == 503
    assert raised.value.detail == "Integration module 'trello' is unavailable"


def test_receive_sms_is_associated_with_module_catalogue():
    assert any(
        module["slug"] == "receive-sms" for module in modules_service.DEFAULT_MODULES
    )


def test_disabled_check_runs_before_call_webhook_side_effects(monkeypatch):
    async def disabled_module(slug: str, *, redact: bool = True):
        return {"slug": slug, "enabled": False}

    async def unexpected_create(**kwargs):
        pytest.fail("disabled webhook created a call event")

    monkeypatch.setattr(modules_service, "get_module", disabled_module)
    monkeypatch.setattr(calls_repo, "create_call_event", unexpected_create)
    app = FastAPI()
    app.include_router(calls_router)

    response = TestClient(app).get("/phonewebhook/token/?event=ringing")

    assert response.status_code == 503
    assert response.json() == {"detail": "Integration module 'calls' is unavailable"}
