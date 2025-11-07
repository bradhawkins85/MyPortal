from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import companies as company_repo
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


def test_delete_company_requires_super_admin(monkeypatch, active_session):
    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.delete(
                "/api/companies/5",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    # This should fail because require_super_admin dependency is not satisfied
    assert response.status_code in [403, 401]


def test_delete_company_removes_company(monkeypatch, active_session):
    async def fake_get_company(company_id):
        return _make_company(id=company_id)

    deleted_company_id = None

    async def fake_delete_company(company_id):
        nonlocal deleted_company_id
        deleted_company_id = company_id

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(company_repo, "delete_company", fake_delete_company)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": active_session.user_id,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.delete(
                "/api/companies/5",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert deleted_company_id == 5


def test_delete_company_returns_404_when_not_found(monkeypatch, active_session):
    async def fake_get_company(company_id):
        return None

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": active_session.user_id,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.delete(
                "/api/companies/999",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_archive_company_requires_super_admin(monkeypatch, active_session):
    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/companies/5/archive",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code in [403, 401]


def test_archive_company_archives_company(monkeypatch, active_session):
    async def fake_get_company(company_id):
        return _make_company(id=company_id, archived=0)

    archived_company = None

    async def fake_archive_company(company_id):
        nonlocal archived_company
        archived_company = _make_company(id=company_id, archived=1)
        return archived_company

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(company_repo, "archive_company", fake_archive_company)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": active_session.user_id,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/companies/5/archive",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert archived_company is not None
    assert archived_company["id"] == 5
    assert archived_company["archived"] == 1


def test_unarchive_company_unarchives_company(monkeypatch, active_session):
    async def fake_get_company(company_id):
        return _make_company(id=company_id, archived=1)

    unarchived_company = None

    async def fake_unarchive_company(company_id):
        nonlocal unarchived_company
        unarchived_company = _make_company(id=company_id, archived=0)
        return unarchived_company

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(company_repo, "unarchive_company", fake_unarchive_company)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": active_session.user_id,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/companies/5/unarchive",
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert unarchived_company is not None
    assert unarchived_company["id"] == 5
    assert unarchived_company["archived"] == 0


def test_list_companies_excludes_archived_by_default(monkeypatch, active_session):
    async def fake_list_companies(include_archived=False):
        if include_archived:
            return [
                _make_company(id=1, name="Active Company", archived=0),
                _make_company(id=2, name="Archived Company", archived=1),
            ]
        else:
            return [
                _make_company(id=1, name="Active Company", archived=0),
            ]

    monkeypatch.setattr(company_repo, "list_companies", fake_list_companies)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": active_session.user_id,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/companies")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    companies = response.json()
    assert len(companies) == 1
    assert companies[0]["name"] == "Active Company"


def test_list_companies_includes_archived_when_requested(monkeypatch, active_session):
    async def fake_list_companies(include_archived=False):
        if include_archived:
            return [
                _make_company(id=1, name="Active Company", archived=0),
                _make_company(id=2, name="Archived Company", archived=1),
            ]
        else:
            return [
                _make_company(id=1, name="Active Company", archived=0),
            ]

    monkeypatch.setattr(company_repo, "list_companies", fake_list_companies)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": active_session.user_id,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/companies?include_archived=true")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    companies = response.json()
    assert len(companies) == 2
    assert companies[0]["name"] == "Active Company"
    assert companies[1]["name"] == "Archived Company"
