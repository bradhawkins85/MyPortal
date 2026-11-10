"""The tray UI gets its own low-privilege token (security review F2)."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.dependencies import auth as auth_deps
from app.api.routes import tray as tray_routes
from app.services import tray as tray_service

UI_ROUTES = {
    "/api/tray/chat-token",
    "/api/tray/ticket-token",
    "/api/tray/outlook-signatures",
    "/api/tray/rmm-script",
    "/api/tray/trmm-script",
}


def _request(token: str = "") -> Request:
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers})


def _route_dependencies(route) -> set:
    return {dep.call for dep in route.dependant.dependencies}


def test_only_ui_routes_accept_the_ui_token():
    ui_routes = set()
    for route in tray_routes.router.routes:
        calls = _route_dependencies(route) if hasattr(route, "dependant") else set()
        if auth_deps.get_tray_ui_device in calls:
            ui_routes.add(route.path)
    assert ui_routes == UI_ROUTES


def test_ui_token_route_needs_the_device_token():
    route = next(r for r in tray_routes.router.routes if getattr(r, "path", "") == "/api/tray/ui-token")
    assert auth_deps.get_current_tray_device in _route_dependencies(route)


def test_migration_adds_ui_token_columns():
    sql = Path("migrations/549_tray_ui_token.sql").read_text()
    assert "-- phase: expand" in sql
    assert "ADD COLUMN IF NOT EXISTS ui_token_hash" in sql
    assert "ADD COLUMN IF NOT EXISTS ui_token_prefix" in sql


@pytest.mark.asyncio
async def test_ui_dependency_accepts_ui_token(monkeypatch):
    device = {"id": 7, "asset_id": 3}
    seen = {}

    async def fake_ui_lookup(token_hash):
        seen["ui"] = token_hash
        return device

    async def fail_auth_lookup(token_hash):
        raise AssertionError("device token lookup not needed")

    monkeypatch.setattr(auth_deps.tray_repo, "get_device_by_ui_token_hash", fake_ui_lookup)
    monkeypatch.setattr(auth_deps.tray_repo, "get_device_by_auth_hash", fail_auth_lookup)

    assert await auth_deps.get_tray_ui_device(_request("ui-secret")) is device
    assert seen["ui"] == tray_service.hash_token("ui-secret")


@pytest.mark.asyncio
async def test_ui_dependency_still_accepts_device_token_from_older_ui(monkeypatch):
    device = {"id": 7, "asset_id": 3}

    async def no_ui_device(token_hash):
        return None

    async def fake_auth_lookup(token_hash):
        return device if token_hash == tray_service.hash_token("device-secret") else None

    monkeypatch.setattr(auth_deps.tray_repo, "get_device_by_ui_token_hash", no_ui_device)
    monkeypatch.setattr(auth_deps.tray_repo, "get_device_by_auth_hash", fake_auth_lookup)

    assert await auth_deps.get_tray_ui_device(_request("device-secret")) is device
    with pytest.raises(HTTPException) as exc:
        await auth_deps.get_tray_ui_device(_request("wrong"))
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc:
        await auth_deps.get_tray_ui_device(_request())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_device_dependency_rejects_ui_token(monkeypatch):
    async def fail_ui_lookup(token_hash):
        raise AssertionError("device endpoints must not look up UI tokens")

    async def no_device(token_hash):
        return None

    monkeypatch.setattr(auth_deps.tray_repo, "get_device_by_ui_token_hash", fail_ui_lookup)
    monkeypatch.setattr(auth_deps.tray_repo, "get_device_by_auth_hash", no_device)

    with pytest.raises(HTTPException) as exc:
        await auth_deps.get_current_tray_device(_request("ui-secret"))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_issue_ui_token_stores_only_the_hash(monkeypatch):
    stored = {}

    async def fake_set(device_id, *, ui_token_hash, ui_token_prefix):
        stored.update(device_id=device_id, hash=ui_token_hash, prefix=ui_token_prefix)

    monkeypatch.setattr(tray_routes.tray_repo, "set_device_ui_token", fake_set)

    response = await tray_routes.issue_ui_token(device={"id": 7})

    assert response.ui_token
    assert stored["device_id"] == 7
    assert stored["hash"] == tray_service.hash_token(response.ui_token)
    assert stored["hash"] != response.ui_token
    assert response.ui_token.startswith(stored["prefix"])


def test_reenrolment_clears_the_ui_token():
    source = Path("app/repositories/tray.py").read_text()
    start = source.index("async def update_device_auth(")
    body = source[start : source.index("async def ", start + 10)]
    assert "ui_token_hash = NULL" in body
