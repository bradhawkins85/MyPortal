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
        "order_number": "ORD123456789012",
        "company_id": 7,
        "status": "pending",
        "shipping_status": "pending",
        "notes": "Review required",
        "po_number": "PO-42",
        "consignment_id": "CONSIGN-1",
        "order_date": datetime(2025, 1, 1, 11, 30, tzinfo=timezone.utc).isoformat(),
        "eta": datetime(2025, 1, 5, 9, 0, tzinfo=timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


def _make_item(**overrides):
    base = {
        "id": 1,
        "company_id": 7,
        "user_id": 3,
        "product_id": 9,
        "product_name": "Example Product",
        "sku": "SKU-1",
        "description": "Sample",
        "image_url": None,
        "quantity": 2,
        "price": Decimal("199.99"),
        "status": "pending",
        "shipping_status": "pending",
        "notes": None,
        "po_number": "PO-42",
        "consignment_id": "CONSIGN-1",
        "order_number": "ORD123456789012",
        "order_date": datetime(2025, 1, 1, 11, 30, tzinfo=timezone.utc).isoformat(),
        "eta": datetime(2025, 1, 5, 9, 0, tzinfo=timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


def test_list_orders_requires_membership_permission(monkeypatch, active_session):
    async def fake_get_company(company_id):
        assert company_id == 7
        return {"id": company_id}

    async def fake_get_membership(user_id, company_id):
        return {"can_access_orders": False}

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(user_company_repo, "get_user_company", fake_get_membership)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/orders", params={"companyId": 7})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_list_orders_returns_summary(monkeypatch, active_session):
    async def fake_get_company(company_id):
        return {"id": company_id}

    async def fake_get_membership(user_id, company_id):
        return {"can_access_orders": True}

    async def fake_list_orders(company_id):
        assert company_id == 7
        return [_make_summary(), _make_summary(order_number="ORD000000000001")]

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(user_company_repo, "get_user_company", fake_get_membership)
    monkeypatch.setattr(shop_repo, "list_order_summaries", fake_list_orders)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/orders", params={"companyId": 7})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["orderNumber"] == "ORD123456789012"


def test_get_order_returns_detail(monkeypatch, active_session):
    async def fake_get_company(company_id):
        return {"id": company_id}

    async def fake_get_membership(user_id, company_id):
        return {"can_access_orders": True}

    async def fake_get_summary(order_number, company_id):
        return _make_summary(order_number=order_number, company_id=company_id)

    async def fake_list_items(order_number, company_id):
        return [_make_item(order_number=order_number, company_id=company_id)]

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(user_company_repo, "get_user_company", fake_get_membership)
    monkeypatch.setattr(shop_repo, "get_order_summary", fake_get_summary)
    monkeypatch.setattr(shop_repo, "list_order_items", fake_list_items)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.get("/api/orders/ORD123456789012", params={"companyId": 7})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["orderNumber"] == "ORD123456789012"
    assert data["items"][0]["productName"] == "Example Product"


def test_update_order_applies_changes(monkeypatch, active_session):
    async def fake_get_company(company_id):
        return {"id": company_id}

    received: dict | None = None

    async def fake_update_order(order_number, company_id, **updates):
        nonlocal received
        received = updates
        summary = _make_summary(order_number=order_number, company_id=company_id)
        summary.update(updates)
        return summary

    async def fake_list_items(order_number, company_id):
        return [_make_item(order_number=order_number, company_id=company_id)]

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(shop_repo, "update_order", fake_update_order)
    monkeypatch.setattr(shop_repo, "list_order_items", fake_list_items)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": active_session.user_id,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.patch(
                "/api/orders/ORD123456789012",
                params={"companyId": 7},
                json={
                    "status": "processing",
                    "shippingStatus": "shipped",
                    "poNumber": "PO-99",
                },
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert received == {
        "status": "processing",
        "shipping_status": "shipped",
        "po_number": "PO-99",
    }
    data = response.json()
    assert data["status"] == "processing"
    assert data["shippingStatus"] == "shipped"


def test_delete_order_requires_super_admin(monkeypatch, active_session):
    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
        "id": active_session.user_id,
        "email": "user@example.com",
        "is_super_admin": False,
    }

    try:
        with TestClient(app) as client:
            response = client.delete(
                "/api/orders/ORD123456789012",
                params={"companyId": 7},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    # This should fail because require_super_admin dependency is not satisfied
    # The endpoint uses require_super_admin, so a non-super-admin user should get 403
    assert response.status_code in [403, 401]


def test_delete_order_removes_order(monkeypatch, active_session):
    async def fake_get_company(company_id):
        return {"id": company_id}

    async def fake_get_summary(order_number, company_id):
        return _make_summary(order_number=order_number, company_id=company_id)

    deleted_order = None

    async def fake_delete_order(order_number, company_id):
        nonlocal deleted_order
        deleted_order = (order_number, company_id)

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(shop_repo, "get_order_summary", fake_get_summary)
    monkeypatch.setattr(shop_repo, "delete_order", fake_delete_order)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": active_session.user_id,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.delete(
                "/api/orders/ORD123456789012",
                params={"companyId": 7},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert deleted_order == ("ORD123456789012", 7)


def test_delete_order_returns_404_when_not_found(monkeypatch, active_session):
    async def fake_get_company(company_id):
        return {"id": company_id}

    async def fake_get_summary(order_number, company_id):
        return None

    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(shop_repo, "get_order_summary", fake_get_summary)

    app.dependency_overrides[database_dependencies.require_database] = lambda: None
    app.dependency_overrides[auth_dependencies.require_super_admin] = lambda: {
        "id": active_session.user_id,
        "email": "admin@example.com",
        "is_super_admin": True,
    }

    try:
        with TestClient(app) as client:
            response = client.delete(
                "/api/orders/ORD999999999999",
                params={"companyId": 7},
                headers={"X-CSRF-Token": active_session.csrf_token},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
