"""Tests for the customisable Dashboard page rendering, including the
Agent quick-ask card which only appears when the Ollama module is enabled."""
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
def authenticated_user(monkeypatch):
    async def fake_require_authenticated_user(request):
        return {"id": 1, "email": "user@example.com", "is_super_admin": False}, None

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_authenticated_user)
    yield


@pytest.fixture
def mock_overview(monkeypatch):
    async def fake_build_consolidated_overview(request, user):
        return {
            "cards": [],
            "catalogue": [],
            "layout": [],
            "grid_columns": 12,
            "company": None,
            "unread_notifications": 0,
            "ollama_enabled": False,
        }

    monkeypatch.setattr(
        main_module, "_build_consolidated_overview", fake_build_consolidated_overview
    )
    yield


@pytest.fixture
def mock_base_context(monkeypatch):
    async def fake_build_base_context(request, user, extra=None):
        context = {
            "request": request,
            "user": user,
            "current_user": user,
            "is_super_admin": user.get("is_super_admin", False),
            "available_companies": [],
            "active_company_id": None,
            "active_company": None,
            "title": "Dashboard",
            "notification_unread_count": 0,
            "plausible_config": {"enabled": False},
            "csrf_token": None,
            "enable_auto_refresh": False,
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)
    yield


def _render_with_ollama(monkeypatch, *, enabled, configured=True):
    async def fake_get_module(slug, redact=True):
        if slug != "ollama":
            return None
        if not configured:
            return None
        return {"slug": "ollama", "enabled": enabled, "settings": {}}

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)


def test_dashboard_renders_customisable_grid(authenticated_user, mock_overview, mock_base_context, monkeypatch):
    """The dashboard exposes the customisable grid scaffolding."""
    _render_with_ollama(monkeypatch, enabled=False)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert 'data-dashboard' in response.text
    assert 'data-dashboard-grid' in response.text
    assert 'data-dashboard-add' in response.text
    assert '/static/js/dashboard.js' in response.text


def test_dashboard_shows_agent_card_when_ollama_enabled(authenticated_user, mock_base_context, monkeypatch):
    """The agent quick-ask card is rendered when Ollama is enabled and the
    overview contains it in the layout."""

    async def fake_overview(request, user):
        return {
            "cards": [
                {
                    "descriptor": {
                        "id": "agent.quick_ask",
                        "title": "Agent quick ask",
                        "description": "",
                        "category": "Knowledge",
                        "default_size": "large",
                        "default_width": 6,
                        "default_height": 3,
                        "template_partial": "partials/dashboard_cards/agent.html",
                        "refresh_interval_seconds": 0,
                    },
                    "position": {"x": 0, "y": 0, "w": 6, "h": 3},
                    "payload": {"available": True},
                }
            ],
            "catalogue": [],
            "layout": [{"id": "agent.quick_ask", "x": 0, "y": 0, "w": 6, "h": 3}],
            "grid_columns": 12,
            "company": None,
            "unread_notifications": 0,
            "ollama_enabled": True,
        }

    monkeypatch.setattr(main_module, "_build_consolidated_overview", fake_overview)
    _render_with_ollama(monkeypatch, enabled=True)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    content = response.text
    assert 'data-dashboard-card="agent.quick_ask"' in content
    assert 'data-agent-panel' in content
    assert '/static/js/agent.js' in content


def test_dashboard_hides_agent_card_when_ollama_disabled(authenticated_user, mock_overview, mock_base_context, monkeypatch):
    _render_with_ollama(monkeypatch, enabled=False)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    content = response.text
    assert 'data-agent-panel' not in content
    assert '/static/js/agent.js' not in content


def test_dashboard_hides_agent_card_when_ollama_not_configured(authenticated_user, mock_overview, mock_base_context, monkeypatch):
    _render_with_ollama(monkeypatch, enabled=False, configured=False)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    content = response.text
    assert 'data-agent-panel' not in content
    assert '/static/js/agent.js' not in content
