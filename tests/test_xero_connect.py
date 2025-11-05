"""Test for /xero/connect endpoint to verify session loading."""
import pytest
from fastapi.testclient import TestClient
from urllib.parse import parse_qs, urlparse

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service
from app.security.session import SessionData


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


def test_xero_connect_redirects_to_login_when_no_session(monkeypatch):
    """Test that xero_connect redirects to login when no session exists."""
    async def fake_load_session(request, *, allow_inactive: bool = False):
        return None

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)

    with TestClient(app) as client:
        response = client.get("/xero/connect", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_xero_connect_calls_load_session(monkeypatch):
    """Test that xero_connect properly calls load_session, not get_session."""
    load_session_called = {"called": False}

    async def fake_load_session(request, *, allow_inactive: bool = False):
        load_session_called["called"] = True
        return None

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)

    with TestClient(app) as client:
        response = client.get("/xero/connect", follow_redirects=False)

    assert load_session_called["called"], "load_session should have been called"
    assert response.status_code == 303


def test_xero_connect_uses_portal_url_for_redirect_uri(monkeypatch):
    """Test that xero_connect uses PORTAL_URL for redirect_uri instead of request URL."""
    from pydantic import AnyHttpUrl
    from datetime import datetime, timezone, timedelta
    
    # Mock PORTAL_URL setting
    monkeypatch.setattr(main_module.settings, "portal_url", AnyHttpUrl("https://myportal.example.com"))
    
    # Mock session with super admin user
    async def fake_load_session(request, *, allow_inactive: bool = False):
        return SessionData(
            id=1,
            user_id=1,
            session_token="test-token",
            csrf_token="test-csrf",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            last_seen_at=datetime.now(timezone.utc),
            ip_address="127.0.0.1",
            user_agent="test-agent"
        )
    
    # Mock user repository
    async def fake_get_user_by_id(user_id: int):
        return {"id": 1, "is_super_admin": True}
    
    # Mock modules service
    async def fake_get_xero_credentials():
        return {"client_id": "test-client-id", "client_secret": "test-secret"}
    
    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(main_module.modules_service, "get_xero_credentials", fake_get_xero_credentials)
    
    with TestClient(app) as client:
        response = client.get("/xero/connect", follow_redirects=False)
    
    assert response.status_code == 303
    
    # Parse the redirect URL to check the redirect_uri parameter
    location = response.headers["location"]
    parsed = urlparse(location)
    query_params = parse_qs(parsed.query)
    
    # Verify the redirect_uri uses PORTAL_URL
    assert "redirect_uri" in query_params
    redirect_uri = query_params["redirect_uri"][0]
    assert redirect_uri == "https://myportal.example.com/xero/callback"
    assert "client_id" in query_params
    assert query_params["client_id"][0] == "test-client-id"

