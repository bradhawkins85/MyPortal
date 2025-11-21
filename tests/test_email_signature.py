"""Test to verify email signature saving functionality."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import users as user_repo
from app.security.session import SessionData, session_manager


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


@pytest.fixture
def active_session(monkeypatch):
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    session = SessionData(
        id=1,
        user_id=123,
        session_token="session-token",
        csrf_token="csrf-token",
        created_at=now,
        expires_at=now,
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
        active_company_id=None,
        pending_totp_secret=None,
    )

    async def fake_load_session(request, *, allow_inactive=False):
        return session

    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    return session


def _make_user(user_id=123, **overrides):
    """Helper to create a test user dict."""
    base = {
        "id": user_id,
        "email": "test@example.com",
        "first_name": "Test",
        "last_name": "User",
        "mobile_phone": None,
        "company_id": None,
        "booking_link_url": None,
        "email_signature": None,
        "created_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        "updated_at": None,
        "force_password_change": 0,
        "is_super_admin": False,
    }
    base.update(overrides)
    return base


def test_update_user_email_signature_via_api(monkeypatch, active_session):
    """Test that email signature can be updated via PATCH /users/{user_id} API."""
    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "test@example.com",
        "is_super_admin": False,
    }

    user_id = active_session.user_id
    signature_html = "<p><strong>Best regards,</strong><br>Test User<br>test@example.com</p>"

    # Mock the repository to return user with signature
    async def fake_get_user_by_id(uid):
        if uid == user_id:
            return _make_user(user_id=user_id)
        return None

    async def fake_update_user(uid, **updates):
        if uid == user_id:
            return _make_user(user_id=user_id, **updates)
        return None

    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(user_repo, "update_user", fake_update_user)

    client = TestClient(app)

    # Test updating email signature
    response = client.patch(
        f"/users/{user_id}",
        json={"email_signature": signature_html},
        headers={"X-CSRF-Token": active_session.csrf_token},
        cookies={"session_token": active_session.session_token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email_signature"] == signature_html

    # Test clearing email signature
    response = client.patch(
        f"/users/{user_id}",
        json={"email_signature": None},
        headers={"X-CSRF-Token": active_session.csrf_token},
        cookies={"session_token": active_session.session_token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email_signature"] is None

    # Clean up dependency overrides
    app.dependency_overrides = {}


def test_user_cannot_update_other_users_signature(monkeypatch, active_session):
    """Test that users can only update their own email signature."""
    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "test@example.com",
        "is_super_admin": False,
    }

    other_user_id = 999
    signature_html = "<p>Test signature</p>"

    async def fake_get_user_by_id(uid):
        if uid == other_user_id:
            return _make_user(user_id=other_user_id)
        return None

    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user_by_id)

    client = TestClient(app)

    # Try to update another user's signature
    response = client.patch(
        f"/users/{other_user_id}",
        json={"email_signature": signature_html},
        headers={"X-CSRF-Token": active_session.csrf_token},
        cookies={"session_token": active_session.session_token},
    )

    # Should be forbidden
    assert response.status_code == 403
    assert "Access denied" in response.json()["detail"]

    # Clean up dependency overrides
    app.dependency_overrides = {}
