"""Tests for preventing duplicate subscription purchases."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service
from app.security.session import SessionData


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

    async def fake_change_log_sync():
        return None

    async def fake_ensure_modules():
        return None

    async def fake_refresh_automations():
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(
        main_module.change_log_service,
        "sync_change_log_sources",
        fake_change_log_sync,
    )
    monkeypatch.setattr(
        main_module.modules_service,
        "ensure_default_modules",
        fake_ensure_modules,
    )
    monkeypatch.setattr(
        main_module.automations_service,
        "refresh_all_schedules",
        fake_refresh_automations,
    )
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)


@pytest.fixture
def active_session(monkeypatch):
    now = datetime.now(timezone.utc)
    session = SessionData(
        id=1,
        user_id=10,
        session_token="session-token",
        csrf_token="csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_company_id=1,
        pending_totp_secret=None,
    )

    async def fake_load_session(request, allow_inactive=False):
        request.state.session = session
        request.state.active_company_id = session.active_company_id
        return session

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    return session


@pytest.fixture
def cart_context(monkeypatch, active_session):
    async def fake_load_context(request, *, permission_field):
        return (
            {"id": active_session.user_id, "email": "user@example.com"},
            {"company_id": 1, "can_access_cart": True},
            {"id": 1, "name": "Example"},
            1,
            None,
        )

    monkeypatch.setattr(
        main_module,
        "_load_company_section_context",
        fake_load_context,
    )


def test_add_to_cart_blocks_owned_subscription(monkeypatch, active_session, cart_context):
    """Test that adding a subscription product that user owns is blocked."""
    
    async def fake_get_product_by_id(product_id, company_id=None):
        return {
            "id": product_id,
            "name": "Test Subscription",
            "sku": "TEST-SUB",
            "stock": 10,
            "price": 99.99,
            "subscription_category_id": 1,  # This makes it a subscription product
        }

    async def fake_get_active_subscription_product_ids(customer_id):
        # Return that customer owns product 123
        return {123}

    monkeypatch.setattr(
        main_module.shop_repo,
        "get_product_by_id",
        fake_get_product_by_id,
    )
    monkeypatch.setattr(
        main_module.subscriptions_repo,
        "get_active_subscription_product_ids",
        fake_get_active_subscription_product_ids,
    )

    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/cart/add",
            data={
                "productId": "123",
                "quantity": "1",
                "_csrf": active_session.csrf_token,
            },
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    assert "/shop" in location
    assert "cart_error" in location
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    error_message = params.get("cart_error", [""])[0]
    assert "already have an active subscription" in error_message.lower()


def test_add_to_cart_allows_non_subscription(monkeypatch, active_session, cart_context):
    """Test that adding a non-subscription product works even if user has other subscriptions."""
    
    async def fake_get_product_by_id(product_id, company_id=None):
        return {
            "id": product_id,
            "name": "Test Product",
            "sku": "TEST-PROD",
            "stock": 10,
            "price": 49.99,
            "subscription_category_id": None,  # Not a subscription
        }

    async def fake_get_active_subscription_product_ids(customer_id):
        # Customer has some other subscription
        return {999}

    async def fake_get_item(session_id, product_id):
        return None

    async def fake_upsert_item(**kwargs):
        return None

    monkeypatch.setattr(
        main_module.shop_repo,
        "get_product_by_id",
        fake_get_product_by_id,
    )
    monkeypatch.setattr(
        main_module.subscriptions_repo,
        "get_active_subscription_product_ids",
        fake_get_active_subscription_product_ids,
    )
    monkeypatch.setattr(main_module.cart_repo, "get_item", fake_get_item)
    monkeypatch.setattr(main_module.cart_repo, "upsert_item", fake_upsert_item)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/cart/add",
            data={
                "productId": "456",
                "quantity": "1",
                "_csrf": active_session.csrf_token,
            },
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    assert "/cart" in location
    assert "cartMessage" in location  # Success message


def test_add_to_cart_allows_unowned_subscription(monkeypatch, active_session, cart_context):
    """Test that adding a subscription product works if user doesn't own it."""
    
    async def fake_get_product_by_id(product_id, company_id=None):
        return {
            "id": product_id,
            "name": "New Subscription",
            "sku": "NEW-SUB",
            "stock": 10,
            "price": 99.99,
            "subscription_category_id": 2,  # This is a subscription
        }

    async def fake_get_active_subscription_product_ids(customer_id):
        # Customer has different subscriptions
        return {999}

    async def fake_get_item(session_id, product_id):
        return None

    async def fake_upsert_item(**kwargs):
        return None

    monkeypatch.setattr(
        main_module.shop_repo,
        "get_product_by_id",
        fake_get_product_by_id,
    )
    monkeypatch.setattr(
        main_module.subscriptions_repo,
        "get_active_subscription_product_ids",
        fake_get_active_subscription_product_ids,
    )
    monkeypatch.setattr(main_module.cart_repo, "get_item", fake_get_item)
    monkeypatch.setattr(main_module.cart_repo, "upsert_item", fake_upsert_item)

    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/cart/add",
            data={
                "productId": "789",
                "quantity": "1",
                "_csrf": active_session.csrf_token,
            },
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    assert "/cart" in location
    assert "cartMessage" in location  # Success message


def test_add_package_blocks_owned_subscription_in_package(monkeypatch, active_session, cart_context):
    """Test that adding a package containing an owned subscription is blocked."""
    
    async def fake_load_company_packages(company_id, is_vip):
        return [
            {
                "id": 1,
                "name": "Test Package",
                "sku": "PKG-001",
                "is_available": True,
                "items": [
                    {
                        "quantity": 1,
                        "resolved_product": {
                            "product_id": 123,
                        },
                    }
                ],
            }
        ]

    async def fake_get_product_by_id(product_id, company_id=None):
        return {
            "id": product_id,
            "name": "Subscription in Package",
            "sku": "SUB-PKG",
            "stock": 10,
            "price": 99.99,
            "subscription_category_id": 1,  # This is a subscription
        }

    async def fake_get_active_subscription_product_ids(customer_id):
        # Customer owns product 123
        return {123}

    monkeypatch.setattr(
        main_module.shop_packages_service,
        "load_company_packages",
        fake_load_company_packages,
    )
    monkeypatch.setattr(
        main_module.shop_repo,
        "get_product_by_id",
        fake_get_product_by_id,
    )
    monkeypatch.setattr(
        main_module.subscriptions_repo,
        "get_active_subscription_product_ids",
        fake_get_active_subscription_product_ids,
    )

    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/cart/add-package",
            data={
                "packageId": "1",
                "quantity": "1",
                "_csrf": active_session.csrf_token,
            },
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    assert "cart_error" in location
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    error_message = params.get("cart_error", [""])[0]
    assert "already have an active subscription" in error_message.lower()
