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
    """Mock an authenticated user."""
    async def fake_require_authenticated_user(request):
        return {"id": 1, "email": "user@example.com", "is_super_admin": False}, None

    monkeypatch.setattr(main_module, "_require_authenticated_user", fake_require_authenticated_user)
    yield


@pytest.fixture
def mock_overview(monkeypatch):
    """Mock the consolidated overview data."""
    async def fake_build_consolidated_overview(request, user):
        return {
            "cards": [],
            "company": None,
            "unread_notifications": 0,
        }

    monkeypatch.setattr(main_module, "_build_consolidated_overview", fake_build_consolidated_overview)
    yield


@pytest.fixture
def mock_base_context(monkeypatch):
    """Mock the base context building to avoid database calls."""
    async def fake_build_base_context(request, user, extra=None):
        context = {
            "request": request,
            "user": user,
            "is_super_admin": user.get("is_super_admin", False),
            "available_companies": [],
            "active_company_id": None,
            "active_company": None,
            "title": "Dashboard",
            "notification_unread_count": 0,
        }
        if extra:
            context.update(extra)
        return context

    monkeypatch.setattr(main_module, "_build_base_context", fake_build_base_context)
    yield


def test_dashboard_shows_agent_section_when_ollama_enabled(authenticated_user, mock_overview, mock_base_context, monkeypatch):
    """Test that the Agent section is visible when Ollama module is enabled."""
    async def fake_get_module(slug, redact=True):
        if slug == "ollama":
            return {"slug": "ollama", "enabled": True, "settings": {}}
        return None

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    content = response.text
    
    # Check that the Agent section is present
    assert "dashboard__section--agent" in content
    assert "agent-panel" in content
    assert "Ask the MyPortal agent" in content
    
    # Check that the agent.js script is loaded
    assert "/static/js/agent.js" in content


def test_dashboard_hides_agent_section_when_ollama_disabled(authenticated_user, mock_overview, mock_base_context, monkeypatch):
    """Test that the Agent section is hidden when Ollama module is disabled."""
    async def fake_get_module(slug, redact=True):
        if slug == "ollama":
            return {"slug": "ollama", "enabled": False, "settings": {}}
        return None

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    content = response.text
    
    # Check that the Agent section is NOT present
    assert "dashboard__section--agent" not in content
    assert "agent-panel" not in content
    assert "Ask the MyPortal agent" not in content
    
    # Check that the agent.js script is NOT loaded
    assert "/static/js/agent.js" not in content


def test_dashboard_hides_agent_section_when_ollama_not_configured(authenticated_user, mock_overview, mock_base_context, monkeypatch):
    """Test that the Agent section is hidden when Ollama module is not configured."""
    async def fake_get_module(slug, redact=True):
        return None  # Module not found

    monkeypatch.setattr(modules_service, "get_module", fake_get_module)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    content = response.text
    
    # Check that the Agent section is NOT present
    assert "dashboard__section--agent" not in content
    assert "agent-panel" not in content
    assert "Ask the MyPortal agent" not in content
    
    # Check that the agent.js script is NOT loaded
    assert "/static/js/agent.js" not in content
