import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, modules_service, scheduler_service


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    async def fake_run_migrations():
        return None

    async def fake_start():
        return None

    async def fake_stop():
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


@pytest.fixture
def super_admin_context(monkeypatch):
    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)
    yield


def test_module_enable_checkbox_sets_true(super_admin_context, monkeypatch):
    calls = []

    async def fake_update_module(slug, *, enabled=None, settings=None):
        calls.append((slug, enabled, settings))
        return {"slug": slug, "enabled": enabled, "settings": settings}

    monkeypatch.setattr(modules_service, "update_module", fake_update_module)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/admin/modules/ollama",
            data={"enabled": "1"},
        )

    assert response.status_code == 303
    assert calls == [("ollama", True, {})]


def test_module_enable_checkbox_absent_sets_false(super_admin_context, monkeypatch):
    calls = []

    async def fake_update_module(slug, *, enabled=None, settings=None):
        calls.append((slug, enabled, settings))
        return {"slug": slug, "enabled": enabled, "settings": settings}

    monkeypatch.setattr(modules_service, "update_module", fake_update_module)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/admin/modules/smtp",
            data={},
        )

    assert response.status_code == 303
    assert calls == [("smtp", False, {})]
