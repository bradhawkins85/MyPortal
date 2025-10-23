import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.api.routes import auth as auth_routes
from app.api.routes import tickets as tickets_routes
from app.core.database import db
from app import main as main_module
from app.main import HELPDESK_PERMISSION_KEY, app, scheduler_service
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
    monkeypatch.setattr(
        main_module.change_log_service,
        "sync_change_log_sources",
        AsyncMock(return_value=None),
    )


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
        auth_routes.company_repo,
        "get_company_by_email_domain",
        AsyncMock(return_value=None),
    )
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


def test_register_assigns_company_by_email_domain(monkeypatch, active_session):
    now = datetime(2025, 1, 1, 8, 0, tzinfo=timezone.utc)
    matched_company = {"id": 12, "name": "Example", "email_domains": ["example.com"]}
    created_user = {
        "id": active_session.user_id,
        "email": "new.user@example.com",
        "first_name": "New",
        "last_name": "User",
        "mobile_phone": None,
        "company_id": matched_company["id"],
        "is_super_admin": False,
        "created_at": now,
        "updated_at": now,
    }
    captured: dict[str, Any] = {}

    async def fake_count_users():
        return 5

    async def fake_get_user_by_email(email: str):
        return None

    async def fake_get_company_by_email_domain(domain: str):
        captured["domain"] = domain
        return matched_company

    async def fake_create_user(**kwargs):
        captured["create_kwargs"] = kwargs
        return created_user

    async def fake_assign_user_to_company(**kwargs):
        captured["assignment"] = kwargs

    async def fake_list_companies_for_user(user_id: int):
        assert user_id == created_user["id"]
        return [
            {
                "company_id": matched_company["id"],
            }
        ]

    async def fake_create_session(user_id, request, active_company_id=None):
        captured["active_company_id"] = active_company_id
        return active_session

    def fake_apply_session_cookies(response, session):
        return None

    monkeypatch.setattr(auth_routes.user_repo, "count_users", fake_count_users)
    monkeypatch.setattr(auth_routes.user_repo, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(auth_routes.user_repo, "create_user", fake_create_user)
    monkeypatch.setattr(
        auth_routes.company_repo,
        "get_company_by_email_domain",
        fake_get_company_by_email_domain,
    )
    monkeypatch.setattr(
        auth_routes.user_company_repo,
        "assign_user_to_company",
        fake_assign_user_to_company,
    )
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
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    data = response.json()
    assert captured.get("domain") == "example.com"
    assert data["user"]["company_id"] == matched_company["id"]
    assert captured["create_kwargs"]["company_id"] == matched_company["id"]
    assert captured["assignment"]["company_id"] == matched_company["id"]
    assert captured["assignment"]["user_id"] == created_user["id"]
    assert captured["active_company_id"] == matched_company["id"]


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

    permission_calls: dict[str, bool] = {}

    async def fake_has_helpdesk_permission(current_user: dict) -> bool:
        permission_calls["called"] = True
        return False

    monkeypatch.setattr(
        tickets_routes,
        "_has_helpdesk_permission",
        fake_has_helpdesk_permission,
    )
    assert tickets_routes._has_helpdesk_permission is fake_has_helpdesk_permission

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
    assert permission_calls.get("called") is True
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

    async def fake_has_helpdesk_permission(current_user: dict) -> bool:
        assert current_user["id"] == 5
        return False

    monkeypatch.setattr(
        tickets_routes,
        "_has_helpdesk_permission",
        fake_has_helpdesk_permission,
    )

    async def fake_has_helpdesk_permission(current_user: dict) -> bool:
        assert current_user["id"] == 5
        return False

    monkeypatch.setattr(
        tickets_routes,
        "_has_helpdesk_permission",
        fake_has_helpdesk_permission,
    )

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

    async def fake_has_helpdesk_permission(current_user: dict) -> bool:
        assert current_user["id"] == active_session.user_id
        return False

    monkeypatch.setattr(
        tickets_routes,
        "_has_helpdesk_permission",
        fake_has_helpdesk_permission,
    )

    async def fake_refresh_summary(ticket_id: int):
        assert ticket_id == ticket["id"]

    async def fake_refresh_tags(ticket_id: int):
        assert ticket_id == ticket["id"]

    monkeypatch.setattr(
        tickets_routes.tickets_service,
        "refresh_ticket_ai_summary",
        fake_refresh_summary,
    )
    monkeypatch.setattr(
        tickets_routes.tickets_service,
        "refresh_ticket_ai_tags",
        fake_refresh_tags,
    )

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
                "body": "Older reply",
                "is_internal": False,
                "created_at": now - timedelta(hours=1),
            },
            {
                "id": 2,
                "ticket_id": ticket_id,
                "author_id": 5,
                "body": "Newer reply",
                "is_internal": False,
                "created_at": now,
            },
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
    assert len(body["replies"]) == 2
    assert body["replies"][0]["body"] == "Newer reply"
    assert body["replies"][1]["body"] == "Older reply"


def test_helpdesk_ticket_listing_allows_global_filters(monkeypatch):
    now = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)
    captured: dict[str, dict] = {}

    async def fake_list_tickets(**kwargs):
        captured["list"] = kwargs
        return [
            {
                "id": 33,
                "subject": "Escalated issue",
                "description": "",
                "status": "open",
                "priority": "high",
                "category": "Incident",
                "module_slug": "billing",
                "company_id": 123,
                "requester_id": 42,
                "assigned_user_id": 8,
                "external_reference": "RMM-1",
                "created_at": now,
                "updated_at": now,
                "closed_at": None,
            }
        ]

    async def fake_count_tickets(**kwargs):
        captured["count"] = kwargs
        return 1

    async def fake_has_helpdesk_permission(current_user: dict) -> bool:
        assert current_user["id"] == 5
        return True

    monkeypatch.setattr(tickets_routes.tickets_repo, "list_tickets", fake_list_tickets)
    monkeypatch.setattr(tickets_routes.tickets_repo, "count_tickets", fake_count_tickets)
    monkeypatch.setattr(
        tickets_routes,
        "_has_helpdesk_permission",
        fake_has_helpdesk_permission,
    )

    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": 5,
        "email": "helpdesk@example.com",
        "is_super_admin": False,
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
    assert captured["list"]["company_id"] == 123
    assert captured["list"]["assigned_user_id"] == 8
    assert captured["list"]["module_slug"] == "billing"
    assert captured["list"]["requester_id"] is None
    assert captured["count"]["company_id"] == 123


def test_helpdesk_can_view_other_ticket(monkeypatch):
    now = datetime(2025, 1, 6, 11, 0, tzinfo=timezone.utc)
    captured: dict[str, object] = {}

    async def fake_get_ticket(ticket_id: int):
        return {
            "id": ticket_id,
            "subject": "Customer outage",
            "description": "",
            "status": "open",
            "priority": "high",
            "category": "Incident",
            "module_slug": None,
            "company_id": 99,
            "requester_id": 77,
            "assigned_user_id": 12,
            "external_reference": None,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
        }

    async def fake_list_replies(ticket_id: int, *, include_internal: bool = True):
        captured["include_internal"] = include_internal
        return []

    async def fake_list_watchers(ticket_id: int):
        captured["watchers"] = True
        return [
            {
                "id": 1,
                "ticket_id": ticket_id,
                "user_id": 12,
                "created_at": now,
            }
        ]

    async def fake_has_helpdesk_permission(current_user: dict) -> bool:
        assert current_user["id"] == 5
        return True

    monkeypatch.setattr(tickets_routes.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_routes.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_routes.tickets_repo, "list_watchers", fake_list_watchers)
    monkeypatch.setattr(
        tickets_routes,
        "_has_helpdesk_permission",
        fake_has_helpdesk_permission,
    )

    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": 5,
        "email": "helpdesk@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/tickets/44")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert captured.get("include_internal") is True
    assert captured.get("watchers") is True
    assert len(body["watchers"]) == 1
    assert body["watchers"][0]["user_id"] == 12


def test_helpdesk_reply_preserves_internal_flag(monkeypatch, active_session):
    now = datetime(2025, 1, 6, 12, 0, tzinfo=timezone.utc)
    ticket = {
        "id": 78,
        "subject": "Escalation",
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

    async def fake_get_ticket(ticket_id: int):
        assert ticket_id == ticket["id"]
        return ticket

    async def fake_create_reply(**kwargs):
        assert kwargs["is_internal"] is True
        return {
            "id": 91,
            "ticket_id": ticket["id"],
            "author_id": active_session.user_id,
            "body": kwargs["body"],
            "is_internal": True,
            "created_at": now,
        }

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return active_session

    async def fake_has_helpdesk_permission(current_user: dict) -> bool:
        assert current_user["id"] == active_session.user_id
        return True

    monkeypatch.setattr(tickets_routes.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_routes.tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(
        tickets_routes,
        "_has_helpdesk_permission",
        fake_has_helpdesk_permission,
    )

    async def fake_refresh_summary(ticket_id: int):
        assert ticket_id == ticket["id"]

    async def fake_refresh_tags(ticket_id: int):
        assert ticket_id == ticket["id"]

    monkeypatch.setattr(
        tickets_routes.tickets_service,
        "refresh_ticket_ai_summary",
        fake_refresh_summary,
    )
    monkeypatch.setattr(
        tickets_routes.tickets_service,
        "refresh_ticket_ai_tags",
        fake_refresh_tags,
    )

    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "helpdesk@example.com",
        "is_super_admin": False,
    }
    app.dependency_overrides[auth_dependencies.get_current_session] = lambda: active_session

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/tickets/{ticket['id']}/replies",
                json={"body": "Internal note", "is_internal": True},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["reply"]["is_internal"] is True
    assert payload["ticket"]["id"] == ticket["id"]


def test_admin_ticket_assign_options_only_include_helpdesk_users(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_list_tickets(**kwargs):
        captured["tickets_kwargs"] = kwargs
        return []

    async def fake_count_tickets(**kwargs):
        captured["count_kwargs"] = kwargs
        return 0

    async def fake_list_modules():
        return []

    async def fake_list_companies():
        return []

    async def fake_list_users():
        return [
            {"id": 1, "email": "tech@example.com"},
            {"id": 2, "email": "general@example.com"},
        ]

    async def fake_list_helpdesk_users(permission: str):
        captured["permission"] = permission
        return [{"id": 1, "email": "tech@example.com"}]

    async def fake_render_template(template_name, request, user, *, extra=None):
        captured["template_name"] = template_name
        captured["extra"] = extra or {}
        return HTMLResponse("ok")

    monkeypatch.setattr(main_module.tickets_repo, "list_tickets", fake_list_tickets)
    monkeypatch.setattr(main_module.tickets_repo, "count_tickets", fake_count_tickets)
    monkeypatch.setattr(main_module.modules_service, "list_modules", fake_list_modules)
    monkeypatch.setattr(main_module.company_repo, "list_companies", fake_list_companies)
    monkeypatch.setattr(main_module.user_repo, "list_users", fake_list_users)
    monkeypatch.setattr(
        main_module.membership_repo,
        "list_users_with_permission",
        fake_list_helpdesk_users,
    )
    monkeypatch.setattr(main_module, "_render_template", fake_render_template)

    request = Request({"type": "http", "app": app, "headers": []})
    user = {"id": 99, "email": "admin@example.com", "is_super_admin": True}

    response = asyncio.run(main_module._render_tickets_dashboard(request, user))

    assert response.status_code == status.HTTP_200_OK
    assert captured["permission"] == HELPDESK_PERMISSION_KEY
    options = captured["extra"].get("ticket_user_options")
    assert options == [{"id": 1, "email": "tech@example.com"}]
    lookup = captured["extra"].get("ticket_user_lookup")
    assert lookup[1]["email"] == "tech@example.com"
    assert lookup[2]["email"] == "general@example.com"
    assert captured["template_name"] == "admin/tickets.html"
