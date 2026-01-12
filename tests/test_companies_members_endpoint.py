from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
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
        user_id=1,
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


def _make_company(**overrides):
    base = {
        "id": 5,
        "name": "Test Company Inc.",
        "syncro_company_id": "12345",
        "tacticalrmm_client_id": "client-123",
        "xero_id": "xero-abc",
        "is_vip": 0,
        "email_domains": ["test.com", "example.com"],
    }
    base.update(overrides)
    return base


def test_list_company_members_returns_members_list(monkeypatch, active_session):
    """Test that the /api/companies/{company_id}/members endpoint returns members in the expected format."""
    async def fake_get_company(company_id):
        return _make_company(id=company_id)

    async def fake_list_company_memberships(company_id):
        return [
            {
                "id": 1,
                "user_id": 10,
                "user_email": "user1@example.com",
                "first_name": "John",
                "last_name": "Doe",
                "status": "active",
            },
            {
                "id": 2,
                "user_id": 20,
                "user_email": "user2@example.com",
                "first_name": "Jane",
                "last_name": "Smith",
                "status": "active",
            },
        ]

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(membership_repo, "list_company_memberships", fake_list_company_memberships)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/companies/5/members")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert "members" in data
    assert len(data["members"]) == 2
    assert data["members"][0]["user_id"] == 10
    assert data["members"][0]["email"] == "user1@example.com"
    assert data["members"][1]["user_id"] == 20
    assert data["members"][1]["email"] == "user2@example.com"


def test_list_company_members_returns_404_for_nonexistent_company(monkeypatch, active_session):
    """Test that the endpoint returns 404 when company doesn't exist."""
    async def fake_get_company(company_id):
        return None

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/companies/999/members")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
