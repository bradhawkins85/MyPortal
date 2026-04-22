"""Tests for the dashboard card registry, layout sanitisation and the
``/api/dashboard`` HTTP endpoints."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service
from app.services import dashboard_cards


# ---------------------------------------------------------------------------
# Shared startup / fixture scaffolding
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(db, "connect", _noop)
    monkeypatch.setattr(db, "disconnect", _noop)
    monkeypatch.setattr(db, "run_migrations", _noop)
    monkeypatch.setattr(scheduler_service, "start", _noop)
    monkeypatch.setattr(scheduler_service, "stop", _noop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


def _make_request(user_id: int = 1, is_super_admin: bool = False, active_company_id: int | None = None):
    state = SimpleNamespace(
        available_companies=[{"company_id": 1, "company_name": "Acme"}] if active_company_id else [],
        active_company_id=active_company_id,
        active_membership=(
            {"is_admin": True, "can_manage_assets": True, "can_manage_invoices": True, "can_manage_licenses": True, "can_manage_staff": True}
            if active_company_id
            else None
        ),
        module_lookup={"ollama": {"slug": "ollama", "enabled": False}},
    )
    return SimpleNamespace(state=state)


# ---------------------------------------------------------------------------
# Registry / permission tests
# ---------------------------------------------------------------------------

def _user(**overrides: Any) -> dict[str, Any]:
    base = {"id": 1, "email": "u@example.com", "is_super_admin": False}
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_registry_contains_expected_cards():
    ids = {c.id for c in dashboard_cards.list_cards()}
    expected = {
        "overview.companies",
        "overview.unread_notifications",
        "tickets.my_open",
        "agent.quick_ask",
        "quick_actions",
        "changelog.recent",
        "notifications.recent",
    }
    assert expected.issubset(ids)


@pytest.mark.asyncio
async def test_super_admin_sees_admin_only_cards():
    request = _make_request(is_super_admin=True)
    ctx = dashboard_cards.CardContext(
        request=request,
        user=_user(is_super_admin=True),
        is_super_admin=True,
        membership=None,
        active_company_id=None,
        available_companies=[],
        module_lookup={"ollama": {"slug": "ollama", "enabled": False}},
    )
    allowed = await dashboard_cards.list_allowed_cards(ctx)
    ids = {c.id for c in allowed}
    assert "overview.portal_users" in ids
    assert "overview.webhook_queue" in ids
    assert "tickets.unassigned" in ids


@pytest.mark.asyncio
async def test_regular_user_cannot_see_super_admin_cards():
    request = _make_request()
    ctx = dashboard_cards.CardContext(
        request=request,
        user=_user(),
        is_super_admin=False,
        membership=None,
        active_company_id=None,
        available_companies=[],
        module_lookup={"ollama": {"slug": "ollama", "enabled": False}},
    )
    allowed = await dashboard_cards.list_allowed_cards(ctx)
    ids = {c.id for c in allowed}
    assert "overview.portal_users" not in ids
    assert "overview.webhook_queue" not in ids
    assert "tickets.unassigned" not in ids


@pytest.mark.asyncio
async def test_membership_flag_required_for_company_cards():
    request = _make_request(active_company_id=1)
    membership = {"can_manage_assets": False, "can_manage_invoices": False, "can_manage_licenses": False, "can_manage_staff": False}
    ctx = dashboard_cards.CardContext(
        request=request,
        user=_user(),
        is_super_admin=False,
        membership=membership,
        active_company_id=1,
        available_companies=[],
        module_lookup={"ollama": {"slug": "ollama", "enabled": False}},
    )
    allowed = await dashboard_cards.list_allowed_cards(ctx)
    ids = {c.id for c in allowed}
    assert "assets.status_mix" not in ids
    assert "invoices.health" not in ids
    assert "licenses.capacity" not in ids
    assert "staff.summary" not in ids


@pytest.mark.asyncio
async def test_agent_card_only_when_ollama_enabled():
    request = _make_request()
    ctx_disabled = dashboard_cards.CardContext(
        request=request,
        user=_user(),
        is_super_admin=False,
        membership=None,
        active_company_id=None,
        available_companies=[],
        module_lookup={"ollama": {"slug": "ollama", "enabled": False}},
    )
    ctx_enabled = dashboard_cards.CardContext(
        request=request,
        user=_user(),
        is_super_admin=False,
        membership=None,
        active_company_id=None,
        available_companies=[],
        module_lookup={"ollama": {"slug": "ollama", "enabled": True}},
    )
    disabled = {c.id for c in await dashboard_cards.list_allowed_cards(ctx_disabled)}
    enabled = {c.id for c in await dashboard_cards.list_allowed_cards(ctx_enabled)}
    assert "agent.quick_ask" not in disabled
    assert "agent.quick_ask" in enabled


# ---------------------------------------------------------------------------
# Layout sanitisation tests
# ---------------------------------------------------------------------------

def test_sanitise_layout_drops_unknown_ids():
    payload = [
        {"id": "tickets.my_open", "x": 0, "y": 0, "w": 4, "h": 2},
        {"id": "does.not.exist", "x": 0, "y": 0, "w": 4, "h": 2},
    ]
    out = dashboard_cards.sanitise_layout(payload)
    assert [entry["id"] for entry in out] == ["tickets.my_open"]


def test_sanitise_layout_filters_disallowed_ids():
    payload = [
        {"id": "tickets.my_open", "x": 0, "y": 0, "w": 4, "h": 2},
        {"id": "overview.portal_users", "x": 0, "y": 0, "w": 4, "h": 2},
    ]
    out = dashboard_cards.sanitise_layout(payload, allowed_ids={"tickets.my_open"})
    assert [entry["id"] for entry in out] == ["tickets.my_open"]


def test_sanitise_layout_clamps_coordinates_and_sizes():
    payload = [
        {"id": "tickets.my_open", "x": -3, "y": -1, "w": 0, "h": 99},
        {"id": "quick_actions", "x": 99, "y": 1000, "w": 99, "h": 99},
    ]
    out = dashboard_cards.sanitise_layout(payload)
    assert out[0]["x"] == 0
    assert out[0]["y"] == 0
    assert out[0]["w"] >= dashboard_cards.MIN_CARD_WIDTH
    assert out[0]["h"] <= dashboard_cards.MAX_CARD_HEIGHT
    assert out[1]["x"] + out[1]["w"] <= dashboard_cards.GRID_COLUMNS
    assert out[1]["y"] <= dashboard_cards.MAX_GRID_ROWS


def test_sanitise_layout_caps_card_count():
    payload = [
        {"id": "tickets.my_open", "x": 0, "y": i, "w": 4, "h": 2}
        for i in range(dashboard_cards.MAX_LAYOUT_CARDS + 5)
    ]
    out = dashboard_cards.sanitise_layout(payload)
    # Duplicates are dropped, so we end up with one entry. Even when ids vary
    # the function caps to MAX_LAYOUT_CARDS.
    assert len(out) <= dashboard_cards.MAX_LAYOUT_CARDS


def test_sanitise_layout_rejects_non_list_payloads():
    assert dashboard_cards.sanitise_layout(None) == []
    assert dashboard_cards.sanitise_layout({"cards": []}) == []
    assert dashboard_cards.sanitise_layout("[]") == []


def test_default_layout_respects_grid_columns():
    layout = dashboard_cards.default_layout({c.id for c in dashboard_cards.list_cards()})
    for entry in layout:
        assert entry["x"] + entry["w"] <= dashboard_cards.GRID_COLUMNS
        assert entry["w"] >= dashboard_cards.MIN_CARD_WIDTH


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------

@pytest.fixture
def authenticated_user(monkeypatch):
    user = {"id": 42, "email": "u@example.com", "is_super_admin": False}

    async def fake_get_current_user():
        return user

    # The api dependency is wired via Depends(get_current_user); FastAPI
    # resolves it from the function signature so we override via app
    # dependency_overrides instead of monkeypatching.
    from app.api.dependencies.auth import get_current_user

    app.dependency_overrides[get_current_user] = fake_get_current_user
    yield user
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def stub_card_context(monkeypatch):
    async def fake_build_context(request, user):
        return dashboard_cards.CardContext(
            request=request,
            user=user,
            is_super_admin=False,
            membership=None,
            active_company_id=None,
            available_companies=[],
            module_lookup={"ollama": {"slug": "ollama", "enabled": False}},
        )

    monkeypatch.setattr(dashboard_cards, "build_card_context", fake_build_context)


@pytest.fixture
def in_memory_layout(monkeypatch):
    storage: dict[tuple[int, str], Any] = {}

    async def fake_get(user_id, key):
        return storage.get((user_id, key))

    async def fake_set(user_id, key, value):
        storage[(user_id, key)] = value
        return value

    async def fake_delete(user_id, key):
        storage.pop((user_id, key), None)

    from app.repositories import user_preferences as repo

    monkeypatch.setattr(repo, "get_preference", fake_get)
    monkeypatch.setattr(repo, "set_preference", fake_set)
    monkeypatch.setattr(repo, "delete_preference", fake_delete)
    return storage


def test_api_catalogue_returns_allowed_cards(authenticated_user, stub_card_context):
    with TestClient(app) as client:
        resp = client.get("/api/dashboard/catalogue")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    ids = {item["id"] for item in data["items"]}
    assert "tickets.my_open" in ids
    assert "overview.portal_users" not in ids


def test_api_layout_returns_default_when_unset(authenticated_user, stub_card_context, in_memory_layout):
    with TestClient(app) as client:
        resp = client.get("/api/dashboard/layout")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_default"] is True
    assert isinstance(data["cards"], list)
    assert all(c["id"] for c in data["cards"])


def test_api_put_layout_persists_and_filters(authenticated_user, stub_card_context, in_memory_layout):
    body = {
        "cards": [
            {"id": "tickets.my_open", "x": 0, "y": 0, "w": 4, "h": 2},
            {"id": "overview.portal_users", "x": 4, "y": 0, "w": 3, "h": 2},  # disallowed for this user
            {"id": "totally.unknown", "x": 0, "y": 0, "w": 3, "h": 2},
        ]
    }
    with TestClient(app) as client:
        resp = client.put("/api/dashboard/layout", json=body)
    assert resp.status_code == 200
    data = resp.json()
    ids = [c["id"] for c in data["cards"]]
    assert ids == ["tickets.my_open"]


def test_api_put_layout_rejects_non_list(authenticated_user, stub_card_context, in_memory_layout):
    with TestClient(app) as client:
        resp = client.put("/api/dashboard/layout", json={"cards": "not a list"})
    assert resp.status_code == 400


def test_api_layout_reset_clears_storage(authenticated_user, stub_card_context, in_memory_layout):
    with TestClient(app) as client:
        # Save first
        client.put(
            "/api/dashboard/layout",
            json={"cards": [{"id": "tickets.my_open", "x": 0, "y": 0, "w": 4, "h": 2}]},
        )
        assert any(in_memory_layout)
        resp = client.post("/api/dashboard/layout/reset")
    assert resp.status_code == 200
    assert not any(in_memory_layout)


def test_api_card_payload_unknown_id_returns_404(authenticated_user, stub_card_context):
    with TestClient(app) as client:
        resp = client.get("/api/dashboard/cards/no-such-card")
    assert resp.status_code == 404


def test_api_card_payload_forbidden_when_user_lacks_permission(authenticated_user, stub_card_context):
    with TestClient(app) as client:
        resp = client.get("/api/dashboard/cards/overview.portal_users")
    assert resp.status_code == 403
