"""Tests for the ``/admin/feature-packs`` admin UI."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.core.features import FeaturePack
from app.main import app, scheduler_service


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(db, "connect", _noop)
    monkeypatch.setattr(db, "disconnect", _noop)
    monkeypatch.setattr(db, "run_migrations", _noop)
    monkeypatch.setattr(scheduler_service, "start", _noop)
    monkeypatch.setattr(scheduler_service, "stop", _noop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)
    # Don't load real packs during these tests.
    monkeypatch.setattr(main_module.settings, "feature_packs", "")


@pytest.fixture
def super_admin_context(monkeypatch):
    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    monkeypatch.setattr(
        main_module, "_require_super_admin_page", fake_require_super_admin_page
    )
    yield


def test_admin_feature_packs_page_renders_with_no_packs(super_admin_context, monkeypatch):
    # Replace the live registry with an empty one for this test.
    from app.core.features import FeatureRegistry

    monkeypatch.setattr(main_module, "feature_registry", FeatureRegistry(app))

    with TestClient(app) as client:
        response = client.get("/admin/feature-packs")

    assert response.status_code == 200
    body = response.text
    assert "Feature packs" in body
    assert "No feature packs are currently loaded" in body


def test_admin_feature_packs_page_lists_loaded_packs(super_admin_context, monkeypatch):
    import asyncio

    from fastapi import APIRouter
    from app.core.features import FeatureRegistry

    test_router = APIRouter()

    @test_router.get("/_test_admin_pack_route")
    async def _route():  # pragma: no cover - dummy
        return {"ok": True}

    fake_pack = FeaturePack(slug="zztest", version="0.1.0", routers=(test_router,))

    fake_registry = FeatureRegistry(app)

    async def _seed() -> None:
        state = await fake_registry._mount(fake_pack)
        # ``_mount`` is the internal primitive; ``load`` also writes
        # into ``_states`` so the public ``list()`` sees the pack.
        fake_registry._states[fake_pack.slug] = state

    asyncio.new_event_loop().run_until_complete(_seed())
    monkeypatch.setattr(main_module, "feature_registry", fake_registry)

    with TestClient(app) as client:
        response = client.get("/admin/feature-packs")

    assert response.status_code == 200
    body = response.text
    assert "zztest" in body
    assert "0.1.0" in body
    # Reload button must be present and target the API endpoint.
    assert 'data-feature-pack-reload="zztest"' in body
    assert "/api/features/" in body


def test_admin_feature_packs_requires_super_admin(monkeypatch):
    async def fake_require_super_admin_page(request):
        from fastapi.responses import RedirectResponse

        return None, RedirectResponse(url="/login", status_code=303)

    monkeypatch.setattr(
        main_module, "_require_super_admin_page", fake_require_super_admin_page
    )

    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/admin/feature-packs")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
