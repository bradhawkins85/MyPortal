from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.api.dependencies.auth import require_super_admin
from app.api.routes import plugins as plugins_api
from app.core.database import db
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
    monkeypatch.setattr(main_module.settings, "feature_packs", "")


def test_enable_disable_plugin_endpoints(monkeypatch):
    calls: list[tuple[str, str]] = []

    class _FakeRegistry:
        async def load(self, slug: str):
            calls.append(("load", slug))

        async def unload(self, slug: str):
            calls.append(("unload", slug))

    async def _fake_set_enabled(slug: str, enabled: bool):
        calls.append(("set", f"{slug}:{enabled}"))

    monkeypatch.setattr(plugins_api, "get_registry", lambda: _FakeRegistry())
    monkeypatch.setattr(plugins_api.plugin_registry_repo, "set_enabled", _fake_set_enabled)
    app.dependency_overrides[require_super_admin] = lambda: {"id": 1, "is_super_admin": True}
    try:
        with TestClient(app) as client:
            response = client.post("/api/plugins/plugin.demo/enable")
            assert response.status_code == 200
            response = client.post("/api/plugins/plugin.demo/disable")
            assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()

    assert ("set", "plugin.demo:True") in calls
    assert ("load", "plugin.demo") in calls
    assert ("set", "plugin.demo:False") in calls
    assert ("unload", "plugin.demo") in calls


def test_install_plugin_requires_single_input():
    app.dependency_overrides[require_super_admin] = lambda: {"id": 1, "is_super_admin": True}
    try:
        with TestClient(app) as client:
            response = client.post("/api/plugins/install")
            assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()
