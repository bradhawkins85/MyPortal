from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import access_activity as access_activity_repo


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    async def _fake_get_module(slug, *, redact=True):
        return None

    monkeypatch.setattr(db, "connect", _noop)
    monkeypatch.setattr(db, "disconnect", _noop)
    monkeypatch.setattr(db, "run_migrations", _noop)
    monkeypatch.setattr(main_module.change_log_service, "sync_change_log_sources", _noop)
    monkeypatch.setattr(main_module.modules_service, "ensure_default_modules", _noop)
    monkeypatch.setattr(main_module.modules_service, "get_module", _fake_get_module)
    monkeypatch.setattr(main_module.automations_service, "refresh_all_schedules", _noop)
    monkeypatch.setattr(scheduler_service, "start", _noop)
    monkeypatch.setattr(scheduler_service, "stop", _noop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


def test_admin_sessions_route_is_registered():
    routes = {
        (getattr(route, "path", None), frozenset(getattr(route, "methods", set())))
        for route in app.routes
    }

    assert ("/admin/sessions", frozenset({"GET"})) in routes


def test_sessions_sidebar_link_and_template_sections_are_present():
    root = Path(__file__).resolve().parent.parent
    base = (root / "app/templates/base.html").read_text()
    page = (root / "app/templates/admin/sessions.html").read_text()

    assert 'href="/admin/sessions"' in base
    assert "{% if is_super_admin %}" in base
    assert "Currently logged-in users" in page
    assert "Recent access activity" in page


def test_admin_sessions_page_renders_activity_tables(monkeypatch):
    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    async def fake_list_active_user_sessions(*, limit=200):
        assert limit == 500
        return [
            {
                "display_name": "Ada Admin",
                "company_name": "Example Co",
                "ip_address": "203.0.113.10",
                "user_agent": "Mozilla/5.0",
                "created_at": datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
                "last_seen_at": datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
                "expires_at": datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc),
            }
        ]

    async def fake_list_recent_connection_activity(*, limit=400, lookback=None):
        assert limit == 1000
        return [
            {
                "access_method": "API key",
                "identity": "Integration key",
                "source": "REST API",
                "source_ip": "198.51.100.25",
                "details": "Requests from this IP: 12",
                "activity_at": datetime(2026, 9, 2, 11, 30, tzinfo=timezone.utc),
            },
            {
                "access_method": "Webhook",
                "identity": "SMTP2Go Delivered",
                "source": "https://portal.example.com/api/webhooks/smtp2go/events",
                "source_ip": "198.51.100.40",
                "details": "Status: succeeded",
                "activity_at": datetime(2026, 9, 2, 8, 30, tzinfo=timezone.utc),
            },
        ]

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)
    monkeypatch.setattr(
        access_activity_repo,
        "list_active_user_sessions",
        fake_list_active_user_sessions,
    )
    monkeypatch.setattr(
        access_activity_repo,
        "list_recent_connection_activity",
        fake_list_recent_connection_activity,
    )

    with TestClient(app) as client:
        response = client.get("/admin/sessions")

    assert response.status_code == 200
    body = response.text
    assert "Currently logged-in users" in body
    assert "Recent access activity" in body
    assert "Ada Admin" in body
    assert "203.0.113.10" in body
    assert "Integration key" in body
    assert "198.51.100.25" in body
    assert "SMTP2Go Delivered" in body


def test_admin_sessions_requires_super_admin(monkeypatch):
    async def fake_require_super_admin_page(request):
        from fastapi.responses import RedirectResponse

        return None, RedirectResponse(url="/login", status_code=303)

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)

    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/admin/sessions")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
