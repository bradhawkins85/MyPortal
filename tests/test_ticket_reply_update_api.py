import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies, database as database_dependencies
from app.core.database import db
from app.api.routes import tickets as tickets_api
from app.main import (
    app,
    automations_service,
    change_log_service,
    modules_service,
    scheduler_service,
    tickets_repo,
    tickets_service,
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

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    async def fake_sync_change_log_sources(*args, **kwargs):
        return None

    async def fake_ensure_default_modules(*args, **kwargs):
        return None

    async def fake_refresh_all_schedules(*args, **kwargs):
        return None

    async def fake_scheduler_start(*args, **kwargs):
        return None

    async def fake_scheduler_stop(*args, **kwargs):
        return None

    monkeypatch.setattr(change_log_service, "sync_change_log_sources", fake_sync_change_log_sources)
    monkeypatch.setattr(modules_service, "ensure_default_modules", fake_ensure_default_modules)
    monkeypatch.setattr(automations_service, "refresh_all_schedules", fake_refresh_all_schedules)
    monkeypatch.setattr(scheduler_service, "start", fake_scheduler_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_scheduler_stop)


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
    app.dependency_overrides[auth_dependencies.require_helpdesk_technician] = lambda: {
        "id": active_session.user_id,
        "email": "tech@example.com",
        "is_super_admin": False,
    }


def _reset_overrides():
    app.dependency_overrides.pop(database_dependencies.require_database, None)
    app.dependency_overrides.pop(auth_dependencies.require_helpdesk_technician, None)


def test_update_reply_time_returns_404_when_ticket_missing(monkeypatch, active_session):
    async def fake_get_ticket(ticket_id):
        return None

    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)

    _override_dependencies(active_session)
    try:
        with TestClient(app) as client:
            response = client.patch(
                "/api/tickets/1/replies/2",
                json={"minutes_spent": 5, "is_billable": True},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        _reset_overrides()

    assert response.status_code == 404


def test_update_reply_time_updates_minutes(monkeypatch, active_session):
    ticket = {
        "id": 5,
        "subject": "Example",
        "description": "",
        "status": "open",
        "priority": "normal",
        "category": None,
        "module_slug": None,
        "company_id": None,
        "requester_id": 7,
        "assigned_user_id": None,
        "external_reference": None,
        "created_at": datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 1, 11, 30, tzinfo=timezone.utc),
        "closed_at": None,
    }

    reply = {
        "id": 11,
        "ticket_id": ticket["id"],
        "author_id": 3,
        "body": "<p>Investigated issue</p>",
        "is_internal": 0,
        "minutes_spent": 8,
        "is_billable": 0,
        "created_at": datetime(2025, 1, 1, 11, 15, tzinfo=timezone.utc),
    }

    async def fake_get_ticket(ticket_id):
        assert ticket_id == ticket["id"]
        return ticket

    async def fake_get_reply_by_id(reply_id):
        assert reply_id == reply["id"]
        return reply

    update_calls = {}

    async def fake_update_reply(reply_id, **kwargs):
        update_calls["reply_id"] = reply_id
        update_calls["kwargs"] = kwargs
        return {
            **reply,
            "minutes_spent": kwargs.get("minutes_spent", reply["minutes_spent"]),
            "is_billable": 1 if kwargs.get("is_billable") else 0,
        }

    async def fake_emit_event(ticket_id, actor_type, actor):
        assert ticket_id == ticket["id"]
        assert actor_type == "technician"
        assert actor["id"] == active_session.user_id

    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_repo, "get_reply_by_id", fake_get_reply_by_id)
    monkeypatch.setattr(tickets_repo, "update_reply", fake_update_reply)
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", fake_emit_event)

    _override_dependencies(active_session)
    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/api/tickets/{ticket['id']}/replies/{reply['id']}",
                json={"minutes_spent": 12, "is_billable": True},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        _reset_overrides()

    assert response.status_code == 200
    data = response.json()
    assert data["reply"]["minutes_spent"] == 12
    assert data["reply"]["is_billable"] is True
    assert data["reply"]["time_summary"] == "12 minutes · Billable"
    assert update_calls["reply_id"] == reply["id"]
    assert update_calls["kwargs"] == {"minutes_spent": 12, "is_billable": True}


def test_update_reply_time_clears_minutes(monkeypatch, active_session):
    ticket = {
        "id": 9,
        "subject": "Example",
        "description": "",
        "status": "open",
        "priority": "normal",
        "category": None,
        "module_slug": None,
        "company_id": None,
        "requester_id": 3,
        "assigned_user_id": None,
        "external_reference": None,
        "created_at": datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 1, 9, 20, tzinfo=timezone.utc),
        "closed_at": None,
    }

    reply = {
        "id": 21,
        "ticket_id": ticket["id"],
        "author_id": 3,
        "body": "<p>Initial fix applied</p>",
        "is_internal": 0,
        "minutes_spent": 15,
        "is_billable": 1,
        "created_at": datetime(2025, 1, 1, 9, 10, tzinfo=timezone.utc),
    }

    async def fake_get_ticket(ticket_id):
        assert ticket_id == ticket["id"]
        return ticket

    async def fake_get_reply_by_id(reply_id):
        assert reply_id == reply["id"]
        return reply

    update_calls = {}

    async def fake_update_reply(reply_id, **kwargs):
        update_calls["reply_id"] = reply_id
        update_calls["kwargs"] = kwargs
        return {
            **reply,
            "minutes_spent": kwargs.get("minutes_spent"),
            "is_billable": 1 if kwargs.get("is_billable", reply["is_billable"]) else 0,
        }

    async def fake_emit_event(ticket_id, actor_type, actor):
        assert ticket_id == ticket["id"]
        assert actor_type == "technician"
        assert actor["id"] == active_session.user_id

    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_repo, "get_reply_by_id", fake_get_reply_by_id)
    monkeypatch.setattr(tickets_repo, "update_reply", fake_update_reply)
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", fake_emit_event)

    _override_dependencies(active_session)
    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/api/tickets/{ticket['id']}/replies/{reply['id']}",
                json={"minutes_spent": None, "is_billable": False},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        _reset_overrides()

    assert response.status_code == 200
    data = response.json()
    assert data["reply"]["minutes_spent"] is None
    assert data["reply"]["is_billable"] is False
    assert data["reply"].get("time_summary") in (None, "")
    assert update_calls["reply_id"] == reply["id"]
    assert update_calls["kwargs"] == {"minutes_spent": None, "is_billable": False}


def test_list_labour_types_returns_records(monkeypatch, active_session):
    async def fake_list_labour_types():
        return [
            {
                "id": 3,
                "code": "REMOTE",
                "name": "Remote support",
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr(tickets_api.labour_types_service, "list_labour_types", fake_list_labour_types)

    result = asyncio.run(
        tickets_api.list_labour_types_endpoint(
            current_user={
                "id": active_session.user_id,
                "email": "tech@example.com",
                "is_super_admin": False,
            }
        )
    )

    assert result.labour_types == [
        tickets_api.LabourTypeModel(
            id=3,
            code="REMOTE",
            name="Remote support",
            created_at=None,
            updated_at=None,
        )
    ]
