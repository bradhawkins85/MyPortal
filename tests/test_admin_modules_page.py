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
    assert calls == [("ollama", True, None)]


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
    assert calls == [("smtp", False, None)]


def test_modules_page_renders_manage_button_from_module_manage_url(super_admin_context, monkeypatch):
    async def fake_list_modules():
        return [
            {
                "slug": "matrix-chat-auto-assign",
                "name": "Matrix Chat Auto-Assign",
                "description": "Auto-assign Matrix chat rooms to technicians.",
                "enabled": True,
                "settings": {"manage_url": "/admin/matrix-chat/auto-assign"},
            },
            {
                "slug": "smtp",
                "name": "Send Email",
                "description": "Send email notifications.",
                "enabled": True,
                "settings": {},
            },
            {
                "slug": "external-link-module",
                "name": "External Link Module",
                "description": "Should not render external manage link.",
                "enabled": True,
                "settings": {"manage_url": "https://example.com/manage"},
            },
        ]

    monkeypatch.setattr(modules_service, "list_modules", fake_list_modules)

    with TestClient(app) as client:
        response = client.get("/admin/modules")

    assert response.status_code == 200
    assert 'href="/admin/matrix-chat/auto-assign"' in response.text
    assert ">Manage</a>" in response.text
    assert 'href="https://example.com/manage"' not in response.text


def test_modules_page_renders_module_quick_actions_and_helpers(super_admin_context, monkeypatch):
    async def fake_list_modules():
        return [
            {"slug": "syncro", "name": "Syncro", "description": "", "enabled": True, "settings": {}},
            {"slug": "tacticalrmm", "name": "Tactical RMM", "description": "", "enabled": True, "settings": {}},
            {"slug": "smtp2go", "name": "SMTP2Go", "description": "", "enabled": True, "settings": {}},
            {"slug": "trello", "name": "Trello", "description": "", "enabled": True, "settings": {}},
            {"slug": "uptimekuma", "name": "Uptime Kuma", "description": "", "enabled": True, "settings": {}},
            {"slug": "xero", "name": "Xero", "description": "", "enabled": True, "settings": {}},
        ]

    monkeypatch.setattr(modules_service, "list_modules", fake_list_modules)

    with TestClient(app) as client:
        response = client.get("/admin/modules")

    assert response.status_code == 200
    assert 'action="/admin/syncro/import-companies"' in response.text
    assert 'action="/admin/modules/tacticalrmm/push-companies"' in response.text
    assert 'action="/admin/modules/tacticalrmm/pull-companies"' in response.text
    assert "/api/webhooks/smtp2go/events" in response.text
    assert "/api/integration-modules/trello/webhook" in response.text
    assert "/api/integration-modules/uptimekuma/alerts" in response.text
    assert "/api/integration-modules/xero/callback" in response.text
    assert 'href="/api/integration-modules/xero/tenants"' in response.text
