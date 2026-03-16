"""Tests for the 'Require PO' option on company edit and cart place-order."""
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
# Helper: base company record
# ---------------------------------------------------------------------------

def _base_company_record(require_po: int = 0) -> dict[str, Any]:
    return {
        "id": 1,
        "name": "Test Company",
        "email_domains": [],
        "syncro_company_id": None,
        "xero_id": None,
        "tacticalrmm_client_id": None,
        "is_vip": 0,
        "payment_method": "invoice_prepay",
        "require_po": require_po,
    }


def _base_monkeypatches(monkeypatch, company_record=None):
    if company_record is None:
        company_record = _base_company_record()
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value=company_record))
    monkeypatch.setattr(
        main, "_get_company_management_scope",
        AsyncMock(return_value=(True, [{"id": 1, "name": "Test Company"}], {})),
    )
    monkeypatch.setattr(main.user_company_repo, "list_assignments", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.role_repo, "list_roles", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.pending_staff_access_repo, "list_assignments_for_company", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.staff_repo, "list_staff_with_users", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.scheduled_tasks_repo, "list_tasks", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.recurring_items_repo, "list_company_recurring_invoice_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.billing_contacts_repo, "list_billing_contacts_for_company", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.staff_repo, "list_staff", AsyncMock(return_value=[]))


# ---------------------------------------------------------------------------
# Company edit page: require_po included in form_data
# ---------------------------------------------------------------------------

async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/companies/1/edit") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


@pytest.mark.anyio
async def test_company_edit_page_require_po_false_by_default(monkeypatch):
    _base_monkeypatches(monkeypatch, _base_company_record(require_po=0))
    captured: dict[str, Any] = {}

    async def fake_render(template_name, request_obj, user_obj, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render)
    await main._render_company_edit_page(_make_request(), {"id": 1}, company_id=1)
    assert captured["extra"]["form_data"]["require_po"] is False


@pytest.mark.anyio
async def test_company_edit_page_require_po_true_when_set(monkeypatch):
    _base_monkeypatches(monkeypatch, _base_company_record(require_po=1))
    captured: dict[str, Any] = {}

    async def fake_render(template_name, request_obj, user_obj, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render)
    await main._render_company_edit_page(_make_request(), {"id": 1}, company_id=1)
    assert captured["extra"]["form_data"]["require_po"] is True


# ---------------------------------------------------------------------------
# Admin update form: require_po saved correctly
# ---------------------------------------------------------------------------

def _admin_update_test(monkeypatch, active_session, form_data: dict) -> dict:
    updated: dict = {}

    async def fake_super(request):
        return {"id": 1}, None

    async def fake_get(company_id):
        return {"id": company_id, "name": "N", "payment_method": "invoice_prepay", "require_po": 0}

    def fake_parse(text):
        return []

    async def fake_update(company_id, **kwargs):
        updated.update(kwargs)

    monkeypatch.setattr(main, "_require_super_admin_page", fake_super)
    monkeypatch.setattr(main.company_repo, "get_company_by_id", fake_get)
    monkeypatch.setattr(main.company_domains, "parse_email_domain_text", fake_parse)
    monkeypatch.setattr(main.company_repo, "update_company", fake_update)

    with TestClient(main.app, follow_redirects=False) as client:
        r = client.post(
            "/admin/companies/1",
            data={"name": "TC", "_csrf": active_session.csrf_token, **form_data},
        )
    assert r.status_code == 303
    return updated


def test_admin_saves_require_po_enabled(monkeypatch, active_session):
    updated = _admin_update_test(
        monkeypatch, active_session, {"invoicePrepay": "1", "requirePo": "1"}
    )
    assert updated.get("require_po") == 1


def test_admin_saves_require_po_disabled(monkeypatch, active_session):
    updated = _admin_update_test(
        monkeypatch, active_session, {"invoicePrepay": "1"}
    )
    assert updated.get("require_po") == 0


# ---------------------------------------------------------------------------
# Cart view: require_po passed to template context
# ---------------------------------------------------------------------------

def _cart_view_test(monkeypatch, active_session, require_po_value) -> Any:
    captured: dict[str, Any] = {}

    async def fake_ctx(request, *, permission_field):
        company: dict[str, Any] = {"id": 1, "name": "E", "is_vip": 0, "payment_method": "invoice_prepay"}
        if require_po_value is not None:
            company["require_po"] = require_po_value
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

    return captured.get("extra", {}).get("require_po")


def test_cart_view_passes_require_po_false(monkeypatch, active_session):
    assert _cart_view_test(monkeypatch, active_session, 0) is False


def test_cart_view_passes_require_po_true(monkeypatch, active_session):
    assert _cart_view_test(monkeypatch, active_session, 1) is True


def test_cart_view_defaults_require_po_false_when_missing(monkeypatch, active_session):
    assert _cart_view_test(monkeypatch, active_session, None) is False


# ---------------------------------------------------------------------------
# Place order: PO required when require_po is set
# ---------------------------------------------------------------------------

def _place_order_test(monkeypatch, active_session, require_po: int, form_data: dict) -> str:
    """Returns the redirect URL after placing (or attempting to place) an order."""

    async def fake_ctx(request, *, permission_field):
        company: dict[str, Any] = {
            "id": 1,
            "name": "E",
            "is_vip": 0,
            "payment_method": "invoice_prepay",
            "require_po": require_po,
        }
        return ({"id": 10}, {"company_id": 1, "can_access_cart": True}, company, 1, None)

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

    monkeypatch.setattr(main, "_load_company_section_context", fake_ctx)
    monkeypatch.setattr(main.cart_repo, "list_items", fake_list_items)
    monkeypatch.setattr(main.shop_repo, "create_order", fake_create_order)
    monkeypatch.setattr(main.cart_repo, "clear_cart", fake_clear_cart)
    monkeypatch.setattr(main.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(main.xero_service, "send_order_to_xero", fake_send_order_to_xero)
    monkeypatch.setattr(main.subscription_shop_integration, "create_subscriptions_from_order", fake_create_subscriptions)
    monkeypatch.setattr(main.shop_service, "maybe_send_stock_notification_by_id", fake_stock_notification)
    # Disable webhook
    monkeypatch.setattr(main.settings, "shop_webhook_url", None)

    with TestClient(main.app, follow_redirects=False) as client:
        r = client.post(
            "/cart/place-order",
            data={"_csrf": active_session.csrf_token, **form_data},
        )
    assert r.status_code == 303
    return r.headers.get("location", "")


def test_place_order_blocked_when_require_po_and_no_po(monkeypatch, active_session):
    location = _place_order_test(monkeypatch, active_session, require_po=1, form_data={})
    assert "orderMessage" in location
    assert "purchase+order" in location or "purchase%20order" in location or "purchase_order" in location or "purchase" in location


def test_place_order_allowed_when_require_po_and_po_provided(monkeypatch, active_session):
    location = _place_order_test(
        monkeypatch, active_session, require_po=1, form_data={"poNumber": "PO-12345"}
    )
    # Should redirect to orders page or confirmation, not back to cart with error
    assert "orderMessage" not in location or "purchase" not in location


def test_place_order_allowed_without_po_when_require_po_disabled(monkeypatch, active_session):
    location = _place_order_test(monkeypatch, active_session, require_po=0, form_data={})
    # Should NOT redirect back to cart with a PO error
    assert "A+purchase+order" not in location
