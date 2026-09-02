from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.security.session import SessionData, session_manager


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def noop():
        return None

    monkeypatch.setattr(db, "connect", noop)
    monkeypatch.setattr(db, "disconnect", noop)
    monkeypatch.setattr(db, "run_migrations", noop)
    monkeypatch.setattr(scheduler_service, "start", noop)
    monkeypatch.setattr(scheduler_service, "stop", noop)


@pytest.fixture
def active_session(monkeypatch):
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
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


def test_browser_logout_revokes_session_and_redirects_to_login(monkeypatch, active_session):
    revoked = []
    cleared = []

    async def fake_revoke_session(session):
        revoked.append(session.id)

    def fake_clear_session_cookies(response):
        cleared.append(response)

    monkeypatch.setattr(session_manager, "revoke_session", fake_revoke_session)
    monkeypatch.setattr(session_manager, "clear_session_cookies", fake_clear_session_cookies)
    app.dependency_overrides[auth_dependencies.get_current_session] = lambda: active_session

    try:
        with TestClient(app) as client:
            response = client.post(
                "/auth/logout",
                data={"_csrf": active_session.csrf_token},
                headers={"Accept": "text/html"},
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert revoked == [active_session.id]
    assert len(cleared) == 1


def test_api_logout_keeps_no_content_response(monkeypatch, active_session):
    async def fake_revoke_session(session):
        return None

    monkeypatch.setattr(session_manager, "revoke_session", fake_revoke_session)
    monkeypatch.setattr(session_manager, "clear_session_cookies", lambda response: None)
    app.dependency_overrides[auth_dependencies.get_current_session] = lambda: active_session

    try:
        with TestClient(app) as client:
            response = client.post(
                "/auth/logout",
                headers={
                    "Accept": "application/json",
                    "X-CSRF-Token": active_session.csrf_token,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert response.content == b""
