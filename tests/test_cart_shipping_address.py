"""Tests for cart shipping address options (address on file, specific address, local pickup)."""

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app import main
from app.core.database import db
from app.security.session import SessionData


# ---------------------------------------------------------------------------
# Startup mock fixture (shared across all tests)
# ---------------------------------------------------------------------------


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
    monkeypatch.setattr(main.change_log_service, "sync_change_log_sources", fake_change_log_sync)
    monkeypatch.setattr(main.modules_service, "ensure_default_modules", fake_ensure_modules)
    monkeypatch.setattr(main.automations_service, "refresh_all_schedules", fake_refresh_automations)
    monkeypatch.setattr(main.scheduler_service, "start", fake_start)
    monkeypatch.setattr(main.scheduler_service, "stop", fake_stop)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helper: active session
# ---------------------------------------------------------------------------


@pytest.fixture
def active_session(monkeypatch):
    now = datetime.now(timezone.utc)
    session = SessionData(
        id=1,
        user_id=10,
        session_token="t",
        csrf_token="csrf",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_company_id=1,
        pending_totp_secret=None,
    )

    async def fake_load(request, allow_inactive=False):
        request.state.session = session
        request.state.active_company_id = 1
        return session

    monkeypatch.setattr(main.session_manager, "load_session", fake_load)
    return session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_company(address: str = "123 Main St") -> dict[str, Any]:
    return {
        "id": 1,
        "name": "Test Company",
        "email_domains": [],
        "syncro_company_id": None,
        "xero_id": None,
        "tacticalrmm_client_id": None,
        "is_vip": 0,
        "payment_method": "invoice_prepay",
        "require_po": 0,
        "address": address,
    }


def _place_order_test(
    monkeypatch,
    active_session,
    form_data: dict,
    *,
    capture_create_order_kwargs: list | None = None,
    capture_create_ticket_kwargs: list | None = None,
) -> str:
    """Returns the redirect location after placing (or attempting) an order."""

    async def fake_ctx(request, *, permission_field):
        return (
            {"id": 10},
            {"company_id": 1, "can_access_cart": True},
            _base_company(),
            1,
            None,
        )

    async def fake_list_items(session_id):
        return [
            {
                "product_id": 5,
                "quantity": 1,
                "unit_price": 10.0,
                "product_name": "Widget",
                "product_sku": "W1",
                "product_vendor_sku": "VW1",
            }
        ]

    async def fake_create_order(**kwargs):
        if capture_create_order_kwargs is not None:
            capture_create_order_kwargs.append(kwargs)
        return (10, 9)

    async def fake_clear_cart(session_id):
        return None

    async def fake_get_user(user_id):
        return {"first_name": "Test", "last_name": "User", "email": "test@example.com"}

    async def fake_send_order_to_xero(**kwargs):
        return None

    async def fake_create_subscriptions(**kwargs):
        return None

    async def fake_stock_notification(product_id, previous_stock, new_stock):
        return None

    async def fake_resolve_ticket_status(status):
        return "open"

    async def fake_create_ticket(**kwargs):
        if capture_create_ticket_kwargs is not None:
            capture_create_ticket_kwargs.append(kwargs)
        return {"id": 123}

    monkeypatch.setattr(main, "_load_company_section_context", fake_ctx)
    monkeypatch.setattr(main.cart_repo, "list_items", fake_list_items)
    monkeypatch.setattr(main.shop_repo, "create_order", fake_create_order)
    monkeypatch.setattr(main.cart_repo, "clear_cart", fake_clear_cart)
    monkeypatch.setattr(main.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(main.xero_service, "send_order_to_xero", fake_send_order_to_xero)
    monkeypatch.setattr(
        main.subscription_shop_integration,
        "create_subscriptions_from_order",
        fake_create_subscriptions,
    )
    monkeypatch.setattr(
        main.shop_service, "maybe_send_stock_notification_by_id", fake_stock_notification
    )
    monkeypatch.setattr(main.tickets_service, "resolve_status_or_default", fake_resolve_ticket_status)
    monkeypatch.setattr(main.tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(main.settings, "shop_webhook_url", None)

    with TestClient(main.app, follow_redirects=False) as client:
        r = client.post(
            "/cart/place-order",
            data={"_csrf": active_session.csrf_token, **form_data},
        )
    assert r.status_code == 303
    return r.headers.get("location", "")


# ---------------------------------------------------------------------------
# Cart view: company_address passed to template context
# ---------------------------------------------------------------------------


def _cart_view_test(monkeypatch, active_session, company_address: str | None) -> Any:
    captured: dict[str, Any] = {}

    async def fake_ctx(request, *, permission_field):
        company: dict[str, Any] = {
            "id": 1,
            "name": "E",
            "is_vip": 0,
            "payment_method": "invoice_prepay",
            "require_po": 0,
        }
        if company_address is not None:
            company["address"] = company_address
        return ({"id": 10}, {"company_id": 1, "can_access_cart": True}, company, 1, None)

    async def fake_list_items(session_id):
        return []

    async def fake_render(template_name, request_obj, user_obj, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_load_company_section_context", fake_ctx)
    monkeypatch.setattr(main.cart_repo, "list_items", fake_list_items)
    monkeypatch.setattr(main, "_render_template", fake_render)

    with TestClient(main.app, follow_redirects=False) as client:
        client.get("/cart")

    return captured.get("extra", {}).get("company_address")


def test_cart_view_passes_company_address(monkeypatch, active_session):
    result = _cart_view_test(monkeypatch, active_session, "42 Example Street")
    assert result == "42 Example Street"


def test_cart_view_passes_empty_string_when_no_address(monkeypatch, active_session):
    result = _cart_view_test(monkeypatch, active_session, None)
    assert result == ""


def test_cart_view_strips_whitespace_from_address(monkeypatch, active_session):
    result = _cart_view_test(monkeypatch, active_session, "  7 Oak Avenue  ")
    assert result == "7 Oak Avenue"


# ---------------------------------------------------------------------------
# Place order: default shipping option is address_on_file
# ---------------------------------------------------------------------------


def test_place_order_defaults_to_address_on_file(monkeypatch, active_session):
    captured: list = []
    location = _place_order_test(
        monkeypatch,
        active_session,
        form_data={},
        capture_create_order_kwargs=captured,
    )
    # Order should succeed (redirect to cart with success message, not an error)
    assert "orderMessage" in location
    assert captured
    assert captured[0]["shipping_option"] == "address_on_file"
    assert captured[0]["shipping_street"] is None


# ---------------------------------------------------------------------------
# Place order: address_on_file shipping option is stored
# ---------------------------------------------------------------------------


def test_place_order_stores_address_on_file_option(monkeypatch, active_session):
    captured: list = []
    _place_order_test(
        monkeypatch,
        active_session,
        form_data={"shippingOption": "address_on_file"},
        capture_create_order_kwargs=captured,
    )
    assert captured[0]["shipping_option"] == "address_on_file"
    assert captured[0]["shipping_street"] is None


# ---------------------------------------------------------------------------
# Place order: local_pickup option is stored
# ---------------------------------------------------------------------------


def test_place_order_stores_local_pickup_option(monkeypatch, active_session):
    captured: list = []
    location = _place_order_test(
        monkeypatch,
        active_session,
        form_data={"shippingOption": "local_pickup"},
        capture_create_order_kwargs=captured,
    )
    # Order should succeed
    assert "orderMessage" in location
    assert captured[0]["shipping_option"] == "local_pickup"
    assert captured[0]["shipping_street"] is None


# ---------------------------------------------------------------------------
# Place order: specific_address requires a street
# ---------------------------------------------------------------------------


def test_place_order_specific_address_blocked_without_street(monkeypatch, active_session):
    location = _place_order_test(
        monkeypatch,
        active_session,
        form_data={"shippingOption": "specific_address"},
    )
    assert "orderMessage" in location
    # The error message mentions a street address requirement
    assert "street" in location.lower() or "address" in location.lower()


def test_place_order_specific_address_allowed_with_street(monkeypatch, active_session):
    captured: list = []
    captured_tickets: list = []
    location = _place_order_test(
        monkeypatch,
        active_session,
        form_data={
            "poNumber": "PO-12345",
            "shippingOption": "specific_address",
            "shippingStreet": "99 Test Lane",
            "shippingCity": "Melbourne",
            "shippingState": "VIC",
            "shippingPostcode": "3000",
            "shippingCountry": "Australia",
        },
        capture_create_order_kwargs=captured,
        capture_create_ticket_kwargs=captured_tickets,
    )
    # Order should succeed (success message, not a street validation error)
    assert "orderMessage" in location
    assert "street" not in location.lower()
    assert captured
    assert captured[0]["shipping_option"] == "specific_address"
    assert captured[0]["shipping_street"] == "99 Test Lane"
    assert captured[0]["shipping_city"] == "Melbourne"
    assert captured[0]["shipping_state"] == "VIC"
    assert captured[0]["shipping_postcode"] == "3000"
    assert captured[0]["shipping_country"] == "Australia"
    assert captured_tickets
    created_ticket = captured_tickets[0]
    assert created_ticket["subject"].startswith("Cart order ORD")
    assert created_ticket["external_reference"].startswith("ORD")
    order_number = created_ticket["external_reference"]
    assert created_ticket["subject"] == f"Cart order {order_number} requires processing"
    assert created_ticket["requester_id"] == 10
    assert created_ticket["company_id"] == 1
    assert created_ticket["category"] == "shop-order"
    description = created_ticket["description"]
    assert "PO-12345" in description
    assert "Widget" in description
    assert "99 Test Lane" in description


def test_place_order_invalid_shipping_option_falls_back(monkeypatch, active_session):
    captured: list = []
    _place_order_test(
        monkeypatch,
        active_session,
        form_data={"shippingOption": "not_a_valid_option"},
        capture_create_order_kwargs=captured,
    )
    assert captured[0]["shipping_option"] == "address_on_file"
