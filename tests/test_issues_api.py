from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.core.database import db
from app.main import (
    app,
    company_repo,
    issues_repo,
    issues_service,
    scheduler_service,
)
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


def _override_dependencies(active_session):
    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_issue_tracker_access] = lambda: {
        "id": active_session.user_id,
        "email": "tech@example.com",
        "is_super_admin": False,
    }


def _reset_overrides():
    app.dependency_overrides.clear()


def _make_assignment(**overrides):
    base = {
        "assignment_id": 2,
        "issue_id": 1,
        "company_id": 3,
        "company_name": "Acme",
        "status": "new",
        "status_label": "New",
        "updated_at": None,
        "updated_at_iso": None,
    }
    base.update(overrides)
    return issues_service.IssueAssignment(**base)


def _make_overview(**overrides):
    base = {
        "issue_id": 1,
        "name": "Printer",
        "slug": None,
        "description": "Paper jams",
        "created_at": None,
        "created_at_iso": None,
        "updated_at": None,
        "updated_at_iso": None,
        "assignments": [_make_assignment()],
    }
    base.update(overrides)
    return issues_service.IssueOverview(**base)


def test_list_issues_returns_payload(monkeypatch, active_session):
    async def fake_build_issue_overview(**kwargs):
        assert kwargs["search"] == "printer"
        assert kwargs["status"] == "resolved"
        assert kwargs["company_id"] == 5
        return [_make_overview()]

    monkeypatch.setattr(issues_service, "build_issue_overview", fake_build_issue_overview)

    _override_dependencies(active_session)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/issues",
                params={"search": "Printer", "status": "resolved", "companyId": 5},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        _reset_overrides()

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "Printer"
    assert payload["items"][0]["assignments"][0]["company_name"] == "Acme"


def test_create_issue_creates_records(monkeypatch, active_session):
    async def fake_ensure_name(name, exclude_issue_id=None):
        assert name == "Printer"

    async def fake_create_issue(**kwargs):
        assert kwargs["name"] == "Printer"
        assert kwargs["description"] == "Paper jams"
        assert kwargs["created_by"] == active_session.user_id
        return {"issue_id": 4, "name": "Printer", "description": "Paper jams"}

    async def fake_get_company(name):
        assert name in {"Acme", "Contoso"}
        return {"id": 10 if name == "Acme" else 11, "name": name}

    assign_calls = []

    async def fake_assign_issue_to_company(**kwargs):
        assign_calls.append(kwargs)
        return {"assignment_id": 1, **kwargs}

    async def fake_get_overview(issue_id):
        assert issue_id == 4
        return _make_overview(issue_id=4)

    monkeypatch.setattr(issues_service, "ensure_issue_name_available", fake_ensure_name)
    monkeypatch.setattr(issues_repo, "create_issue", fake_create_issue)
    monkeypatch.setattr(company_repo, "get_company_by_name", fake_get_company)
    monkeypatch.setattr(issues_repo, "assign_issue_to_company", fake_assign_issue_to_company)
    monkeypatch.setattr(issues_service, "get_issue_overview", fake_get_overview)

    _override_dependencies(active_session)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/issues",
                json={
                    "name": "Printer",
                    "description": "Paper jams",
                    "companies": [
                        {"company_name": "Acme", "status": "new"},
                        {"company_name": "Contoso", "status": "investigating"},
                    ],
                },
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        _reset_overrides()

    assert response.status_code == 201
    assert len(assign_calls) == 2
    assert assign_calls[0]["status"] == "new"
    assert assign_calls[1]["status"] == "investigating"
    payload = response.json()
    assert payload["name"] == "Printer"


def test_update_issue_status_returns_assignment(monkeypatch, active_session):
    async def fake_upsert(**kwargs):
        assert kwargs["issue_name"] == "Printer"
        assert kwargs["company_name"] == "Acme"
        assert kwargs["status"] == "resolved"
        return _make_assignment(status="resolved", status_label="Resolved")

    monkeypatch.setattr(issues_service, "upsert_issue_status_by_name", fake_upsert)

    _override_dependencies(active_session)
    try:
        with TestClient(app) as client:
            response = client.put(
                "/api/issues/status",
                json={
                    "issue_name": "Printer",
                    "company_name": "Acme",
                    "status": "resolved",
                },
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        _reset_overrides()

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "resolved"
    assert data["status_label"] == "Resolved"
