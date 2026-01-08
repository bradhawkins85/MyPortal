from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import auth as auth_dependencies
from app.api.dependencies import database as database_dependencies
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import companies as company_repo
from app.repositories import shop as shop_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import users as users_repo
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


@pytest.mark.asyncio
async def test_assign_quote_as_super_admin(monkeypatch, active_session):
    """Test that super admin can assign quotes."""
    company = {"id": 7, "name": "Acme Corp"}
    quote_summary = {
        "quote_number": "QUO123",
        "company_id": 7,
        "user_id": 1,
        "status": "active",
        "assigned_user_id": None,
    }
    assigned_user = {"id": 2, "email": "user2@example.com"}

    async def fake_get_company_by_id(company_id):
        return company if company_id == 7 else None

    async def fake_get_quote_summary(quote_number, company_id):
        if quote_number == "QUO123" and company_id == 7:
            return quote_summary
        return None

    async def fake_get_user_by_id(user_id):
        if user_id == 2:
            return assigned_user
        return None

    async def fake_get_user_company(user_id, company_id):
        if user_id == 2 and company_id == 7:
            return {"user_id": 2, "company_id": 7}
        return None

    async def fake_assign_quote(quote_number, company_id, assigned_user_id):
        updated = dict(quote_summary)
        updated["assigned_user_id"] = assigned_user_id
        return updated

    async def fake_list_quote_items(quote_number, company_id):
        return []

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(shop_repo, "get_quote_summary", fake_get_quote_summary)
    monkeypatch.setattr(users_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(user_company_repo, "get_user_company", fake_get_user_company)
    monkeypatch.setattr(shop_repo, "assign_quote", fake_assign_quote)
    monkeypatch.setattr(shop_repo, "list_quote_items", fake_list_quote_items)

    def override_database():
        pass

    def override_current_user():
        return {"id": 1, "is_super_admin": True}

    app.dependency_overrides[database_dependencies.require_database] = override_database
    app.dependency_overrides[auth_dependencies.get_current_user] = override_current_user

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/quotes/QUO123/assign?companyId=7",
                json={"assignedUserId": 2}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["assignedUserId"] == 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_assign_quote_as_company_admin(monkeypatch, active_session):
    """Test that company admin can assign quotes."""
    company = {"id": 7, "name": "Acme Corp"}
    quote_summary = {
        "quote_number": "QUO123",
        "company_id": 7,
        "user_id": 1,
        "status": "active",
        "assigned_user_id": None,
    }
    assigned_user = {"id": 2, "email": "user2@example.com"}

    async def fake_get_company_by_id(company_id):
        return company if company_id == 7 else None

    async def fake_get_quote_summary(quote_number, company_id):
        if quote_number == "QUO123" and company_id == 7:
            return quote_summary
        return None

    async def fake_get_user_by_id(user_id):
        if user_id == 2:
            return assigned_user
        return None

    async def fake_get_user_company(user_id, company_id):
        # User 1 is admin, User 2 is member
        if user_id == 1 and company_id == 7:
            return {"user_id": 1, "company_id": 7, "is_admin": True}
        if user_id == 2 and company_id == 7:
            return {"user_id": 2, "company_id": 7, "is_admin": False}
        return None

    async def fake_assign_quote(quote_number, company_id, assigned_user_id):
        updated = dict(quote_summary)
        updated["assigned_user_id"] = assigned_user_id
        return updated

    async def fake_list_quote_items(quote_number, company_id):
        return []

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(shop_repo, "get_quote_summary", fake_get_quote_summary)
    monkeypatch.setattr(users_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(user_company_repo, "get_user_company", fake_get_user_company)
    monkeypatch.setattr(shop_repo, "assign_quote", fake_assign_quote)
    monkeypatch.setattr(shop_repo, "list_quote_items", fake_list_quote_items)

    def override_database():
        pass

    def override_current_user():
        return {"id": 1, "is_super_admin": False}

    app.dependency_overrides[database_dependencies.require_database] = override_database
    app.dependency_overrides[auth_dependencies.get_current_user] = override_current_user

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/quotes/QUO123/assign?companyId=7",
                json={"assignedUserId": 2}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["assignedUserId"] == 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_assign_quote_permission_denied(monkeypatch, active_session):
    """Test that regular users cannot assign quotes."""
    company = {"id": 7, "name": "Acme Corp"}
    quote_summary = {
        "quote_number": "QUO123",
        "company_id": 7,
        "user_id": 1,
        "status": "active",
        "assigned_user_id": None,
    }

    async def fake_get_company_by_id(company_id):
        return company if company_id == 7 else None

    async def fake_get_quote_summary(quote_number, company_id):
        if quote_number == "QUO123" and company_id == 7:
            return quote_summary
        return None

    async def fake_get_user_company(user_id, company_id):
        # User is not admin
        if user_id == 1 and company_id == 7:
            return {"user_id": 1, "company_id": 7, "is_admin": False}
        return None

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(shop_repo, "get_quote_summary", fake_get_quote_summary)
    monkeypatch.setattr(user_company_repo, "get_user_company", fake_get_user_company)

    def override_database():
        pass

    def override_current_user():
        return {"id": 1, "is_super_admin": False}

    app.dependency_overrides[database_dependencies.require_database] = override_database
    app.dependency_overrides[auth_dependencies.get_current_user] = override_current_user

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/quotes/QUO123/assign?companyId=7",
                json={"assignedUserId": 2}
            )
            assert response.status_code == 403
            assert "super admins and company admins" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_unassign_quote(monkeypatch, active_session):
    """Test that admins can unassign quotes."""
    company = {"id": 7, "name": "Acme Corp"}
    quote_summary = {
        "quote_number": "QUO123",
        "company_id": 7,
        "user_id": 1,
        "status": "active",
        "assigned_user_id": 2,
    }

    async def fake_get_company_by_id(company_id):
        return company if company_id == 7 else None

    async def fake_get_quote_summary(quote_number, company_id):
        if quote_number == "QUO123" and company_id == 7:
            return quote_summary
        return None

    async def fake_assign_quote(quote_number, company_id, assigned_user_id):
        updated = dict(quote_summary)
        updated["assigned_user_id"] = assigned_user_id
        return updated

    async def fake_list_quote_items(quote_number, company_id):
        return []

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(shop_repo, "get_quote_summary", fake_get_quote_summary)
    monkeypatch.setattr(shop_repo, "assign_quote", fake_assign_quote)
    monkeypatch.setattr(shop_repo, "list_quote_items", fake_list_quote_items)

    def override_database():
        pass

    def override_current_user():
        return {"id": 1, "is_super_admin": True}

    app.dependency_overrides[database_dependencies.require_database] = override_database
    app.dependency_overrides[auth_dependencies.get_current_user] = override_current_user

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/quotes/QUO123/assign?companyId=7",
                json={"assignedUserId": None}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["assignedUserId"] is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_assign_quote_to_non_member(monkeypatch, active_session):
    """Test that quotes cannot be assigned to users not in the company."""
    company = {"id": 7, "name": "Acme Corp"}
    quote_summary = {
        "quote_number": "QUO123",
        "company_id": 7,
        "user_id": 1,
        "status": "active",
        "assigned_user_id": None,
    }
    non_member_user = {"id": 3, "email": "user3@example.com"}

    async def fake_get_company_by_id(company_id):
        return company if company_id == 7 else None

    async def fake_get_quote_summary(quote_number, company_id):
        if quote_number == "QUO123" and company_id == 7:
            return quote_summary
        return None

    async def fake_get_user_by_id(user_id):
        if user_id == 3:
            return non_member_user
        return None

    async def fake_get_user_company(user_id, company_id):
        # User 3 is not a member of company 7
        return None

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(shop_repo, "get_quote_summary", fake_get_quote_summary)
    monkeypatch.setattr(users_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(user_company_repo, "get_user_company", fake_get_user_company)

    def override_database():
        pass

    def override_current_user():
        return {"id": 1, "is_super_admin": True}

    app.dependency_overrides[database_dependencies.require_database] = override_database
    app.dependency_overrides[auth_dependencies.get_current_user] = override_current_user

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/quotes/QUO123/assign?companyId=7",
                json={"assignedUserId": 3}
            )
            assert response.status_code == 400
            assert "not a member" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_assigned_user_can_access_quote(monkeypatch, active_session):
    """Test that assigned users can access their assigned quotes."""
    company = {"id": 7, "name": "Acme Corp"}
    quote_summary = {
        "quote_number": "QUO123",
        "company_id": 7,
        "user_id": 1,
        "status": "active",
        "assigned_user_id": 2,
    }

    async def fake_get_company_by_id(company_id):
        return company if company_id == 7 else None

    async def fake_get_quote_summary(quote_number, company_id):
        if quote_number == "QUO123" and company_id == 7:
            return quote_summary
        return None

    async def fake_get_user_company(user_id, company_id):
        # User 2 does not have can_access_quotes permission
        if user_id == 2 and company_id == 7:
            return {"user_id": 2, "company_id": 7, "can_access_quotes": False}
        return None

    async def fake_list_quote_items(quote_number, company_id):
        return []

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(shop_repo, "get_quote_summary", fake_get_quote_summary)
    monkeypatch.setattr(user_company_repo, "get_user_company", fake_get_user_company)
    monkeypatch.setattr(shop_repo, "list_quote_items", fake_list_quote_items)

    def override_database():
        pass

    def override_current_user():
        return {"id": 2, "is_super_admin": False}

    app.dependency_overrides[database_dependencies.require_database] = override_database
    app.dependency_overrides[auth_dependencies.get_current_user] = override_current_user

    try:
        with TestClient(app) as client:
            response = client.get("/api/quotes/QUO123?companyId=7")
            assert response.status_code == 200
            data = response.json()
            assert data["quoteNumber"] == "QUO123"
            assert data["assignedUserId"] == 2
    finally:
        app.dependency_overrides.clear()
