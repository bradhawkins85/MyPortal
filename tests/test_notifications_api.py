from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.core.database import db
from app.main import app, notifications_repo, scheduler_service
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
        expires_at=now + timedelta(hours=1),
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


def _make_notification_record(**overrides):
    base = {
        "id": 1,
        "user_id": 42,
        "event_type": "system",
        "message": "Test notification",
        "metadata": {"example": True},
        "created_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        "read_at": None,
    }
    base.update(overrides)
    return base


def test_create_notification_requires_super_admin(monkeypatch, active_session):
    async def fake_create_notification(**_kwargs):
        return _make_notification_record()

    monkeypatch.setattr(notifications_repo, "create_notification", fake_create_notification)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": 7,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/notifications",
                json={"event_type": "system", "message": "Denied"},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_create_notification_returns_created_record(monkeypatch, active_session):
    created = _make_notification_record(id=99, message="Created via API")

    async def fake_create_notification(**kwargs):
        assert kwargs["event_type"] == "system"
        assert kwargs["message"] == "Created via API"
        assert kwargs["user_id"] == 123
        assert kwargs["metadata"] == {"source": "api"}
        return created

    monkeypatch.setattr(notifications_repo, "create_notification", fake_create_notification)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": 1,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/notifications",
                json={
                    "event_type": "system",
                    "message": "Created via API",
                    "user_id": 123,
                    "metadata": {"source": "api"},
                },
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == created["id"]
    assert data["message"] == created["message"]
    assert data["user_id"] == created["user_id"]
    assert data["metadata"] == created["metadata"]
