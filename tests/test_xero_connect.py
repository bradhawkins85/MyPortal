"""Test for /xero/connect endpoint to verify session loading."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service


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
