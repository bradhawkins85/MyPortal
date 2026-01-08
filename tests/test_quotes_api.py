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


def _make_summary(**overrides):
    base = {
        "quote_number": "QUO123456789012",
        "company_id": 7,
        "status": "active",
        "notes": "Test quote",
        "po_number": "PO-42",
        "created_at": datetime(2025, 1, 1, 11, 30, tzinfo=timezone.utc).isoformat(),
        "expires_at": datetime(2025, 1, 8, 11, 30, tzinfo=timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


def _make_item(**overrides):
    base = {
        "id": 1,
        "company_id": 7,
        "user_id": 1,
        "product_id": 42,
        "product_name": "Widget Pro",
        "sku": "WID-PRO-001",
        "description": "Professional widget",
        "image_url": None,
        "quantity": 5,
        "price": Decimal("99.99"),
        "status": "active",
        "notes": None,
        "po_number": "PO-42",
        "quote_number": "QUO123456789012",
        "created_at": datetime(2025, 1, 1, 11, 30, tzinfo=timezone.utc),
        "expires_at": datetime(2025, 1, 8, 11, 30, tzinfo=timezone.utc),
        "stock": 100,
        "stock_nsw": 30,
        "stock_qld": 25,
        "stock_vic": 25,
        "stock_sa": 20,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_list_quotes_endpoint(monkeypatch, active_session):
    company = {"id": 7, "name": "Acme Corp"}

    async def fake_get_company_by_id(company_id):
        return company if company_id == 7 else None

    async def fake_get_user_company(user_id, company_id):
        return {"can_access_quotes": True}

    async def fake_list_quote_summaries(company_id):
        return [_make_summary()]

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(user_company_repo, "get_user_company", fake_get_user_company)
    monkeypatch.setattr(shop_repo, "list_quote_summaries", fake_list_quote_summaries)

    def override_database():
        pass

    def override_current_user():
        return {"id": 1, "is_super_admin": False}

    app.dependency_overrides[database_dependencies.require_database] = override_database
    app.dependency_overrides[auth_dependencies.get_current_user] = override_current_user

    try:
        with TestClient(app) as client:
            response = client.get("/api/quotes?companyId=7")
            assert response.status_code == 200
            quotes = response.json()
            assert len(quotes) == 1
            assert quotes[0]["quoteNumber"] == "QUO123456789012"
            assert quotes[0]["companyId"] == 7
            assert quotes[0]["status"] == "active"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_quote_endpoint(monkeypatch, active_session):
    company = {"id": 7, "name": "Acme Corp"}
    quote_summary = _make_summary()
    items = [_make_item()]

    async def fake_get_company_by_id(company_id):
        return company if company_id == 7 else None

    async def fake_get_user_company(user_id, company_id):
        return {"can_access_quotes": True}

    async def fake_get_quote_summary(quote_number, company_id):
        if quote_number == "QUO123456789012" and company_id == 7:
            return quote_summary
        return None

    async def fake_list_quote_items(quote_number, company_id):
        if quote_number == "QUO123456789012" and company_id == 7:
            return items
        return []

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(user_company_repo, "get_user_company", fake_get_user_company)
    monkeypatch.setattr(shop_repo, "get_quote_summary", fake_get_quote_summary)
    monkeypatch.setattr(shop_repo, "list_quote_items", fake_list_quote_items)

    def override_database():
        pass

    def override_current_user():
        return {"id": 1, "is_super_admin": False}

    app.dependency_overrides[database_dependencies.require_database] = override_database
    app.dependency_overrides[auth_dependencies.get_current_user] = override_current_user

    try:
        with TestClient(app) as client:
            response = client.get("/api/quotes/QUO123456789012?companyId=7")
            assert response.status_code == 200
            data = response.json()
            assert data["quoteNumber"] == "QUO123456789012"
            assert data["companyId"] == 7
            assert len(data["items"]) == 1
            assert data["items"][0]["productName"] == "Widget Pro"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_quotes_permission_denied(monkeypatch, active_session):
    company = {"id": 7, "name": "Acme Corp"}

    async def fake_get_company_by_id(company_id):
        return company if company_id == 7 else None

    async def fake_get_user_company(user_id, company_id):
        return {"can_access_quotes": False}

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company_by_id)
    monkeypatch.setattr(user_company_repo, "get_user_company", fake_get_user_company)

    def override_database():
        pass

    def override_current_user():
        return {"id": 1, "is_super_admin": False}

    app.dependency_overrides[database_dependencies.require_database] = override_database
    app.dependency_overrides[auth_dependencies.get_current_user] = override_current_user

    try:
        with TestClient(app) as client:
            response = client.get("/api/quotes?companyId=7")
            assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
