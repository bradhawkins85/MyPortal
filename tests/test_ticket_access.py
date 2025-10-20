import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.api.routes import auth as auth_routes
from app.api.routes import tickets as tickets_routes
from app.core.database import db
from app.main import app, scheduler_service
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
def active_session() -> SessionData:
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    return SessionData(
        id=7,
        user_id=5,
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


def test_register_allows_general_signup_after_first_user(monkeypatch, active_session):
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    created_user = {
        "id": active_session.user_id,
        "email": "new.user@example.com",
        "first_name": "New",
        "last_name": "User",
        "mobile_phone": None,
        "company_id": None,
        "is_super_admin": False,
        "created_at": now,
        "updated_at": now,
    }
    call_args: dict[str, object] = {}

    async def fake_count_users():
        return 3

    async def fake_get_user_by_email(email: str):
        assert email == created_user["email"]
        return None

    async def fake_create_user(**kwargs):
        call_args.update(kwargs)
        return created_user

    async def fake_list_companies_for_user(user_id: int):
        assert user_id == created_user["id"]
        return []

    async def fake_create_session(user_id, request, active_company_id=None):
        assert user_id == created_user["id"]
        call_args["active_company_id"] = active_company_id
        return active_session

    def fake_apply_session_cookies(response, session):
        return None

    monkeypatch.setattr(auth_routes.user_repo, "count_users", fake_count_users)
    monkeypatch.setattr(auth_routes.user_repo, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(auth_routes.user_repo, "create_user", fake_create_user)
    monkeypatch.setattr(
        auth_routes.user_company_repo,
        "list_companies_for_user",
        fake_list_companies_for_user,
    )
    monkeypatch.setattr(auth_routes.session_manager, "create_session", fake_create_session)
    monkeypatch.setattr(auth_routes.session_manager, "apply_session_cookies", fake_apply_session_cookies)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None

    try:
        with TestClient(app) as client:
            response = client.post(
                "/auth/register",
                json={
                    "email": created_user["email"],
                    "password": "strong-password",
                    "first_name": created_user["first_name"],
                    "last_name": created_user["last_name"],
                    "company_id": 999,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    data = response.json()
    assert data["user"]["email"] == created_user["email"]
    assert data["user"]["is_super_admin"] is False
    assert call_args.get("is_super_admin") is False
    assert call_args.get("company_id") is None
    assert call_args.get("active_company_id") is None


def test_non_admin_ticket_listing_scoped_to_requester(monkeypatch):
    now = datetime(2025, 1, 2, 9, 30, tzinfo=timezone.utc)
    captured: dict[str, dict] = {}

    async def fake_list_tickets(**kwargs):
        captured["list"] = kwargs
        return [
            {
                "id": 10,
                "subject": "Example ticket",
                "description": "Example",
                "status": "open",
                "priority": "normal",
                "category": None,
                "module_slug": None,
                "company_id": None,
                "requester_id": 5,
                "assigned_user_id": None,
                "external_reference": None,
                "created_at": now,
                "updated_at": now,
                "closed_at": None,
            }
        ]

    async def fake_count_tickets(**kwargs):
        captured["count"] = kwargs
        return 1

    monkeypatch.setattr(tickets_routes.tickets_repo, "list_tickets", fake_list_tickets)
    monkeypatch.setattr(tickets_routes.tickets_repo, "count_tickets", fake_count_tickets)

    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": 5,
        "email": "user@example.com",
        "is_super_admin": False,
        "company_id": None,
    }

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/tickets",
                params={
                    "company_id": 123,
                    "assigned_user_id": 8,
                    "module_slug": "billing",
                    "status": "open",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["list"]["requester_id"] == 5
    assert captured["list"]["company_id"] is None
    assert captured["list"]["assigned_user_id"] is None
    assert captured["list"]["module_slug"] is None
    assert captured["count"]["requester_id"] == 5
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["requester_id"] == 5


def test_non_admin_cannot_view_other_ticket(monkeypatch):
    now = datetime(2025, 1, 3, 8, 45, tzinfo=timezone.utc)

    async def fake_get_ticket(ticket_id: int):
        return {
            "id": ticket_id,
            "subject": "Other ticket",
            "description": "",
            "status": "open",
            "priority": "normal",
            "category": None,
            "module_slug": None,
            "company_id": None,
            "requester_id": 99,
            "assigned_user_id": None,
            "external_reference": None,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
        }

    async def fake_list_replies(*args, **kwargs):
        raise AssertionError("Replies should not be fetched for forbidden tickets")

    async def fake_list_watchers(*args, **kwargs):
        raise AssertionError("Watchers should not be fetched for forbidden tickets")

    monkeypatch.setattr(tickets_routes.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_routes.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_routes.tickets_repo, "list_watchers", fake_list_watchers)

    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": 5,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/tickets/1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_non_admin_reply_forces_public_visibility(monkeypatch, active_session):
    now = datetime(2025, 1, 4, 10, 15, tzinfo=timezone.utc)
    ticket = {
        "id": 20,
        "subject": "Example",
        "description": "",
        "status": "open",
        "priority": "normal",
        "category": None,
        "module_slug": None,
        "company_id": None,
        "requester_id": active_session.user_id,
        "assigned_user_id": None,
        "external_reference": None,
        "created_at": now,
        "updated_at": now,
        "closed_at": None,
    }

    async def fake_get_ticket(ticket_id: int):
        assert ticket_id == ticket["id"]
        return ticket

    async def fake_create_reply(**kwargs):
        assert kwargs["is_internal"] is False
        return {
            "id": 55,
            "ticket_id": ticket["id"],
            "author_id": active_session.user_id,
            "body": kwargs["body"],
            "is_internal": False,
            "created_at": now,
        }

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return active_session

    monkeypatch.setattr(tickets_routes.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_routes.tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(session_manager, "load_session", fake_load_session)

    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
        "is_super_admin": False,
    }
    app.dependency_overrides[auth_dependencies.get_current_session] = lambda: active_session

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/tickets/{ticket['id']}/replies",
                json={"body": "Reply", "is_internal": True},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["reply"]["is_internal"] is False
    assert payload["ticket"]["id"] == ticket["id"]


def test_ticket_detail_filters_private_replies_for_requester(monkeypatch):
    now = datetime(2025, 1, 5, 9, 0, tzinfo=timezone.utc)
    captured: dict[str, bool] = {}

    async def fake_get_ticket(ticket_id: int):
        return {
            "id": ticket_id,
            "subject": "My ticket",
            "description": "",
            "status": "open",
            "priority": "normal",
            "category": None,
            "module_slug": None,
            "company_id": None,
            "requester_id": 5,
            "assigned_user_id": None,
            "external_reference": None,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
        }

    async def fake_list_replies(ticket_id: int, *, include_internal: bool = True):
        captured["include_internal"] = include_internal
        return [
            {
                "id": 1,
                "ticket_id": ticket_id,
                "author_id": 5,
                "body": "Public reply",
                "is_internal": False,
                "created_at": now,
            }
        ]

    async def fake_list_watchers(*args, **kwargs):
        raise AssertionError("Watchers should not be fetched for requester views")

    monkeypatch.setattr(tickets_routes.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_routes.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_routes.tickets_repo, "list_watchers", fake_list_watchers)

    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": 5,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/tickets/77")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert captured.get("include_internal") is False
    assert body["watchers"] == []
    assert len(body["replies"]) == 1
    assert body["replies"][0]["body"] == "Public reply"
