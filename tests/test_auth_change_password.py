import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import users as user_repo
from app.security.passwords import hash_password
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

    monkeypatch.setattr(db, 'connect', fake_connect)
    monkeypatch.setattr(db, 'disconnect', fake_disconnect)
    monkeypatch.setattr(db, 'run_migrations', fake_run_migrations)
    monkeypatch.setattr(scheduler_service, 'start', fake_start)
    monkeypatch.setattr(scheduler_service, 'stop', fake_stop)


@pytest.fixture
def active_session(monkeypatch):
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    session = SessionData(
        id=1,
        user_id=1,
        session_token='token',
        csrf_token='csrf-token',
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

    monkeypatch.setattr(session_manager, 'load_session', fake_load_session)
    return session


def test_change_password_updates_hash(monkeypatch, active_session):
    password_hash = hash_password('current-password-123')
    user = {
        'id': 42,
        'email': 'admin@example.com',
        'password_hash': password_hash,
    }
    recorded = {}

    async def fake_set_password(user_id, new_password):
        recorded['user_id'] = user_id
        recorded['password'] = new_password

    monkeypatch.setattr(user_repo, 'set_user_password', fake_set_password)

    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: user

    try:
        with TestClient(app) as client:
            response = client.post(
                '/auth/password/change',
                json={'current_password': 'current-password-123', 'new_password': 'new-password-4567'},
                headers={'X-CSRF-Token': active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert recorded['user_id'] == 42
    assert recorded['password'] == 'new-password-4567'


def test_change_password_rejects_invalid_current_password(monkeypatch, active_session):
    password_hash = hash_password('current-password-123')
    user = {
        'id': 42,
        'email': 'admin@example.com',
        'password_hash': password_hash,
    }
    called = {'count': 0}

    async def fake_set_password(user_id, new_password):
        called['count'] += 1

    monkeypatch.setattr(user_repo, 'set_user_password', fake_set_password)

    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: user

    try:
        with TestClient(app) as client:
            response = client.post(
                '/auth/password/change',
                json={'current_password': 'wrong-password', 'new_password': 'new-password-4567'},
                headers={'X-CSRF-Token': active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()['detail'] == 'Current password is incorrect'
    assert called['count'] == 0
