from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.security.session import SessionData, session_manager
from app.services import impersonation as impersonation_service


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
        id=10,
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
        impersonator_user_id=None,
        impersonator_session_id=None,
        impersonation_started_at=None,
    )

    async def fake_load_session(request, *, allow_inactive=False):
        request.state.session = session
        return session

    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    return session


def test_impersonate_user_returns_login_response(monkeypatch, active_session):
    new_now = datetime(2025, 1, 1, 12, 5, tzinfo=timezone.utc)
    impersonated_session = SessionData(
        id=99,
        user_id=5,
        session_token="impersonated",
        csrf_token="csrf-imp",
        created_at=new_now,
        expires_at=new_now + timedelta(hours=1),
        last_seen_at=new_now,
        ip_address=None,
        user_agent=None,
        active_company_id=None,
        pending_totp_secret=None,
        impersonator_user_id=1,
        impersonator_session_id=10,
        impersonation_started_at=new_now,
    )
    impersonated_user = {"id": 5, "email": "user@example.com"}

    async def fake_start_impersonation(**kwargs):
        return impersonated_user, impersonated_session

    applied_sessions = []

    def fake_apply_cookies(response, session):
        applied_sessions.append(session.id)

    monkeypatch.setattr(impersonation_service, "start_impersonation", fake_start_impersonation)
    monkeypatch.setattr(session_manager, "apply_session_cookies", fake_apply_cookies)

    async def override_current_session(request: Request):
        request.state.session = active_session
        return active_session

    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": 1,
        "email": "admin@example.com",
        "is_super_admin": True,
    }
    app.dependency_overrides[auth_dependencies.get_current_session] = override_current_session

    try:
        with TestClient(app) as client:
            client.cookies.set(session_manager.session_cookie_name, active_session.session_token)
            client.cookies.set(session_manager.csrf_cookie_name, active_session.csrf_token)
            response = client.post(
                "/auth/impersonate",
                json={"user_id": 5},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["user"]["id"] == impersonated_user["id"]
    assert payload["session"]["impersonator_user_id"] == active_session.user_id
    assert applied_sessions == [impersonated_session.id]


def test_impersonate_user_conflict(monkeypatch, active_session):
    async def fake_start_impersonation(**kwargs):
        raise impersonation_service.AlreadyImpersonatingError("active")

    monkeypatch.setattr(impersonation_service, "start_impersonation", fake_start_impersonation)
    monkeypatch.setattr(session_manager, "apply_session_cookies", lambda *args, **kwargs: None)

    async def override_current_session(request: Request):
        request.state.session = active_session
        return active_session

    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": 1,
        "email": "admin@example.com",
        "is_super_admin": True,
    }
    app.dependency_overrides[auth_dependencies.get_current_session] = override_current_session

    try:
        with TestClient(app) as client:
            client.cookies.set(session_manager.session_cookie_name, active_session.session_token)
            client.cookies.set(session_manager.csrf_cookie_name, active_session.csrf_token)
            response = client.post(
                "/auth/impersonate",
                json={"user_id": 5},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409


def test_exit_impersonation_restores_original_session(monkeypatch, active_session):
    impersonated_session = SessionData(
        id=40,
        user_id=5,
        session_token="impersonated",
        csrf_token="csrf-imp",
        created_at=active_session.created_at,
        expires_at=active_session.expires_at,
        last_seen_at=active_session.last_seen_at,
        ip_address=None,
        user_agent=None,
        active_company_id=None,
        pending_totp_secret=None,
        impersonator_user_id=active_session.user_id,
        impersonator_session_id=active_session.id,
        impersonation_started_at=active_session.created_at,
    )
    restored_session = active_session
    restored_user = {"id": active_session.user_id, "email": "admin@example.com"}

    async def fake_end_impersonation(**kwargs):
        return restored_user, restored_session

    applied_sessions = []

    def fake_apply_cookies(response, session):
        applied_sessions.append(session.id)

    monkeypatch.setattr(impersonation_service, "end_impersonation", fake_end_impersonation)
    monkeypatch.setattr(session_manager, "apply_session_cookies", fake_apply_cookies)

    async def fake_load_impersonated_session(request, *, allow_inactive=False):
        request.state.session = impersonated_session
        return impersonated_session

    monkeypatch.setattr(session_manager, "load_session", fake_load_impersonated_session)

    async def override_impersonated_session(request: Request):
        request.state.session = impersonated_session
        return impersonated_session

    app.dependency_overrides[auth_dependencies.get_current_session] = override_impersonated_session

    try:
        with TestClient(app) as client:
            client.cookies.set(session_manager.session_cookie_name, impersonated_session.session_token)
            client.cookies.set(session_manager.csrf_cookie_name, impersonated_session.csrf_token)
            response = client.post(
                "/auth/impersonation/exit",
                headers={"X-CSRF-Token": impersonated_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["id"] == restored_user["id"]
    assert payload["session"]["user_id"] == restored_session.user_id
    assert applied_sessions == [restored_session.id]
