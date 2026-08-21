"""Regression tests for the super-admin Voice Monitor admin page."""

from __future__ import annotations

from html import unescape
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.features.voice_monitor import admin_routes
from app.api.dependencies import modules as module_dependencies
from app.core.config import Settings
from app.core.database import db
from app.core.features import discover_builtin_feature_pack_slugs
from app.main import app, scheduler_service


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(db, "connect", _noop)
    monkeypatch.setattr(db, "disconnect", _noop)
    monkeypatch.setattr(db, "run_migrations", _noop)
    monkeypatch.setattr(scheduler_service, "start", _noop)
    monkeypatch.setattr(scheduler_service, "stop", _noop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)
    monkeypatch.setattr(
        main_module.settings,
        "feature_packs",
        ",".join(discover_builtin_feature_pack_slugs()),
    )

    class _FakePluginLoader:
        async def list_admin_rows(self, registry):
            return []

    monkeypatch.setattr(main_module, "get_plugin_loader", lambda: _FakePluginLoader())


@pytest.fixture
def super_admin_context(monkeypatch):
    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    monkeypatch.setattr(
        main_module, "_require_super_admin_page", fake_require_super_admin_page
    )
    monkeypatch.setattr(
        admin_routes.company_repo,
        "list_companies",
        AsyncMock(return_value=[{"id": 7, "name": "Example Co"}]),
    )
    monkeypatch.setattr(
        admin_routes.subscriptions_repo,
        "list_subscriptions",
        AsyncMock(
            return_value=[
                {
                    "id": "sub-1",
                    "customer_id": 7,
                    "category_name": "Voice Monitor",
                    "product_name": "Voice Monitor",
                    "quantity": 2,
                }
            ]
        ),
    )
    yield


@pytest.fixture
def module_enabled_context(monkeypatch):
    async def fake_get_module(slug, redact=False):
        assert slug == "voice-monitor"
        return {"slug": slug, "enabled": True}

    monkeypatch.setattr(
        module_dependencies.modules_service, "get_module", fake_get_module
    )
    yield


@pytest.fixture
def module_disabled_context(monkeypatch):
    async def fake_get_module(slug, redact=False):
        assert slug == "voice-monitor"
        return {"slug": slug, "enabled": False}

    monkeypatch.setattr(
        module_dependencies.modules_service, "get_module", fake_get_module
    )
    yield


def test_admin_voice_monitor_page_renders_for_super_admin_when_module_enabled(
    super_admin_context, module_enabled_context
):
    with TestClient(app) as client:
        response = client.get("/admin/voice-monitor")

    assert response.status_code == 200
    assert "Voice Monitor" in response.text
    assert "Add monitored number" in response.text
    assert "Example Co" in response.text
    assert "/admin/voice-monitor/endpoints?company_id=" in response.text
    assert 'href="/admin/voice-monitor/subscriptions"' in response.text
    assert 'href="/admin/subscriptions"' not in response.text


def test_voice_monitor_subscriptions_page_manages_monitored_numbers(
    super_admin_context, module_enabled_context
):
    with TestClient(app) as client:
        response = client.get("/admin/voice-monitor/subscriptions")

    assert response.status_code == 200
    assert "Manage Voice Monitor Subscriptions" in response.text
    assert "Add monitored number" in response.text
    assert "Phone number (E.164)" in response.text
    assert "/admin/voice-monitor/endpoints?company_id=" in response.text


def test_voice_monitor_subscriptions_page_shows_form_without_subscriptions(
    monkeypatch, module_enabled_context
):
    """The subscriptions page must show the add-number form even with no subscriptions."""
    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    monkeypatch.setattr(
        main_module, "_require_super_admin_page", fake_require_super_admin_page
    )
    monkeypatch.setattr(
        admin_routes.company_repo,
        "list_companies",
        AsyncMock(return_value=[{"id": 7, "name": "Example Co"}]),
    )
    monkeypatch.setattr(
        admin_routes.subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=[]),
    )

    with TestClient(app) as client:
        response = client.get("/admin/voice-monitor/subscriptions")

    assert response.status_code == 200
    assert "Manage Voice Monitor Subscriptions" in response.text
    assert "Add monitored number" in response.text
    assert "Phone number (E.164)" in response.text
    assert "/admin/voice-monitor/endpoints?company_id=" in response.text


def test_admin_voice_monitor_page_is_unavailable_when_module_disabled(
    super_admin_context, module_disabled_context
):
    with TestClient(app) as client:
        response = client.get("/admin/voice-monitor")

    assert response.status_code == 503
    assert "Integration module 'voice-monitor' is unavailable" in unescape(
        response.text
    )


def test_settings_keep_builtin_voice_monitor_pack_when_legacy_feature_packs_env_is_present(
    monkeypatch,
):
    monkeypatch.setenv("FEATURE_PACKS", "tickets")
    settings = Settings()

    assert "tickets" in settings.feature_packs.split(",")
    assert "voice_monitor" in settings.feature_packs.split(",")
