"""Regression tests for the super-admin Voice Monitor admin page."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
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
    monkeypatch.setattr(main_module.settings, "feature_packs", "voice_monitor")

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
    yield


def test_admin_voice_monitor_page_renders_for_super_admin_when_module_disabled(
    super_admin_context,
):
    with TestClient(app) as client:
        response = client.get("/admin/voice-monitor")

    assert response.status_code == 200
    assert "Voice Monitor" in response.text
