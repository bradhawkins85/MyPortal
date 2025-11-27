"""Tests for email tracking status endpoint authentication.

These tests verify that the /api/email-tracking/status/{tracking_id} endpoint
requires authentication to access sensitive email tracking data.
"""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.api.dependencies import auth as auth_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.services import email_tracking


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    """Mock database and scheduler for testing."""
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
def mock_authenticated_user():
    """Fixture to override auth dependency with a mock authenticated user."""
    mock_user = {
        "id": 1,
        "email": "user@example.com",
        "is_super_admin": False,
    }
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: mock_user
    yield mock_user
    app.dependency_overrides.clear()


def test_tracking_status_requires_authentication(monkeypatch):
    """Test that unauthenticated requests are rejected with 401."""
    with TestClient(app) as client:
        response = client.get(
            "/api/email-tracking/status/some-tracking-id",
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_tracking_status_returns_data_when_authenticated(monkeypatch, mock_authenticated_user):
    """Test that authenticated requests can access tracking status."""
    async def fake_get_tracking_status(tracking_id: str):
        return {
            "sent_at": None,
            "opened_at": None,
            "open_count": 0,
            "is_opened": False,
        }

    monkeypatch.setattr(email_tracking, "get_tracking_status", fake_get_tracking_status)

    with TestClient(app) as client:
        response = client.get("/api/email-tracking/status/test-tracking-id")

    assert response.status_code == 200
    data = response.json()
    assert data["tracking_id"] == "test-tracking-id"
    assert data["found"] is True


def test_tracking_status_not_found_when_authenticated(monkeypatch, mock_authenticated_user):
    """Test that authenticated user gets proper 'not found' response for missing tracking ID."""
    async def fake_get_tracking_status(tracking_id: str):
        return None

    monkeypatch.setattr(email_tracking, "get_tracking_status", fake_get_tracking_status)

    with TestClient(app) as client:
        response = client.get("/api/email-tracking/status/nonexistent-id")

    assert response.status_code == 200
    data = response.json()
    assert data["tracking_id"] == "nonexistent-id"
    assert data["found"] is False
    assert data["error"] == "Tracking ID not found"


def test_tracking_pixel_does_not_require_authentication(monkeypatch):
    """Test that tracking pixel endpoint remains public (by design).
    
    The tracking pixel must remain unauthenticated because it is embedded
    in emails and loaded by email clients.
    """
    async def fake_record_tracking_event(**kwargs):
        pass

    async def fake_send_event_to_plausible(**kwargs):
        pass

    monkeypatch.setattr(email_tracking, "record_tracking_event", fake_record_tracking_event)
    monkeypatch.setattr(email_tracking, "send_event_to_plausible", fake_send_event_to_plausible)

    with TestClient(app) as client:
        response = client.get("/api/email-tracking/pixel/test-id.gif")

    # Should succeed without authentication (returns the tracking pixel GIF)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"


def test_tracking_click_does_not_require_authentication(monkeypatch):
    """Test that click tracking endpoint remains public (by design).
    
    The click tracking must remain unauthenticated because it is used
    in email links clicked by users who may not be logged in.
    """
    async def fake_record_tracking_event(**kwargs):
        pass

    async def fake_send_event_to_plausible(**kwargs):
        pass

    monkeypatch.setattr(email_tracking, "record_tracking_event", fake_record_tracking_event)
    monkeypatch.setattr(email_tracking, "send_event_to_plausible", fake_send_event_to_plausible)

    with TestClient(app, follow_redirects=False) as client:
        response = client.get(
            "/api/email-tracking/click",
            params={"tid": "test-id", "url": "https://example.com"}
        )

    # Should succeed without authentication (redirects to target URL)
    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com"
