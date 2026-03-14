"""Tests for company payment method setting (Invoice Prepay/Postpay/Stripe)."""
import pytest
from typing import Any
from unittest.mock import AsyncMock
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app import main


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/companies/1/edit") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def fake_connect(): return None
    async def fake_disconnect(): return None
    async def fake_run_migrations(): return None
    async def fake_start(): return None
    async def fake_stop(): return None
    async def fake_change_log_sync(): return None
    async def fake_ensure_modules(): return None
    async def fake_refresh_automations(): return None
    from app.core.database import db
    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(main.change_log_service, "sync_change_log_sources", fake_change_log_sync)
    monkeypatch.setattr(main.modules_service, "ensure_default_modules", fake_ensure_modules)
    monkeypatch.setattr(main.automations_service, "refresh_all_schedules", fake_refresh_automations)
    monkeypatch.setattr(main.scheduler_service, "start", fake_start)
    monkeypatch.setattr(main.scheduler_service, "stop", fake_stop)


def _base_company_record(payment_method: str = "invoice_prepay") -> dict[str, Any]:
    return {
        "id": 1, "name": "Test Company", "email_domains": [],
        "syncro_company_id": None, "xero_id": None,
        "tacticalrmm_client_id": None, "is_vip": 0,
        "payment_method": payment_method,
    }


def _base_monkeypatches(monkeypatch, company_record=None):
    if company_record is None:
        company_record = _base_company_record()
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value=company_record))
    monkeypatch.setattr(main, "_get_company_management_scope", AsyncMock(return_value=(True, [{"id": 1, "name": "Test Company"}], {})))
    monkeypatch.setattr(main.user_company_repo, "list_assignments", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.role_repo, "list_roles", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.pending_staff_access_repo, "list_assignments_for_company", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.staff_repo, "list_staff_with_users", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.scheduled_tasks_repo, "list_tasks", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.recurring_items_repo, "list_company_recurring_invoice_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.billing_contacts_repo, "list_billing_contacts_for_company", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.staff_repo, "list_staff", AsyncMock(return_value=[]))


# -- company edit page renders ------------------------------------------------

@pytest.mark.anyio
async def test_company_edit_page_includes_payment_method_invoice_prepay(monkeypatch):
    _base_monkeypatches(monkeypatch, _base_company_record("invoice_prepay"))
    captured: dict[str, Any] = {}
    async def fake_render(template_name, request_obj, user_obj, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")
    monkeypatch.setattr(main, "_render_template", fake_render)
    await main._render_company_edit_page(_make_request(), {"id": 1}, company_id=1)
    assert captured["extra"]["form_data"]["payment_method"] == "invoice_prepay"


@pytest.mark.anyio
async def test_company_edit_page_includes_payment_method_invoice_postpay(monkeypatch):
    _base_monkeypatches(monkeypatch, _base_company_record("invoice_postpay"))
    captured: dict[str, Any] = {}
    async def fake_render(template_name, request_obj, user_obj, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")
    monkeypatch.setattr(main, "_render_template", fake_render)
    await main._render_company_edit_page(_make_request(), {"id": 1}, company_id=1)
    assert captured["extra"]["form_data"]["payment_method"] == "invoice_postpay"


@pytest.mark.anyio
async def test_company_edit_page_includes_all_three_payment_methods(monkeypatch):
    _base_monkeypatches(monkeypatch, _base_company_record("invoice_prepay,invoice_postpay,stripe"))
    captured: dict[str, Any] = {}
    async def fake_render(template_name, request_obj, user_obj, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")
    monkeypatch.setattr(main, "_render_template", fake_render)
    await main._render_company_edit_page(_make_request(), {"id": 1}, company_id=1)
    assert captured["extra"]["form_data"]["payment_method"] == "invoice_prepay,invoice_postpay,stripe"


@pytest.mark.anyio
async def test_company_edit_page_defaults_payment_method_when_missing(monkeypatch):
    _base_monkeypatches(monkeypatch, {
        "id": 1, "name": "TC", "email_domains": [],
        "syncro_company_id": None, "xero_id": None,
        "tacticalrmm_client_id": None, "is_vip": 0,
    })
    captured: dict[str, Any] = {}
    async def fake_render(template_name, request_obj, user_obj, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")
    monkeypatch.setattr(main, "_render_template", fake_render)
    await main._render_company_edit_page(_make_request(), {"id": 1}, company_id=1)
    assert captured["extra"]["form_data"]["payment_method"] == "invoice_prepay"


# -- cart view ----------------------------------------------------------------

@pytest.fixture
def active_session(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.security.session import SessionData
    now = datetime.now(timezone.utc)
    session = SessionData(
        id=1, user_id=10, session_token="t", csrf_token="csrf",
        created_at=now, expires_at=now + timedelta(hours=1),
        last_seen_at=now, ip_address="127.0.0.1", user_agent="pytest",
        active_company_id=1, pending_totp_secret=None,
    )
    async def fake_load(request, allow_inactive=False):
        request.state.session = session
        request.state.active_company_id = 1
        return session
    monkeypatch.setattr(main.session_manager, "load_session", fake_load)
    return session


def _cart_test(monkeypatch, active_session, payment_method):
    captured: dict[str, Any] = {}
    async def fake_ctx(request, *, permission_field):
        company = {"id": 1, "name": "E", "is_vip": 0}
        if payment_method is not None:
            company["payment_method"] = payment_method
        return ({"id": 10}, {"company_id": 1, "can_access_cart": True}, company, 1, None)
    async def fake_list_items(session_id): return []
    async def fake_render(template_name, request_obj, user_obj, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")
    monkeypatch.setattr(main, "_load_company_section_context", fake_ctx)
    monkeypatch.setattr(main.cart_repo, "list_items", fake_list_items)
    monkeypatch.setattr(main, "_render_template", fake_render)
    from fastapi.testclient import TestClient
    with TestClient(main.app, follow_redirects=False) as client:
        client.get("/cart")
    return captured.get("extra", {}).get("payment_method")


def test_cart_view_passes_invoice_prepay(monkeypatch, active_session):
    assert _cart_test(monkeypatch, active_session, "invoice_prepay") == "invoice_prepay"


def test_cart_view_passes_invoice_postpay(monkeypatch, active_session):
    assert _cart_test(monkeypatch, active_session, "invoice_postpay") == "invoice_postpay"


def test_cart_view_passes_invoice_postpay_and_stripe(monkeypatch, active_session):
    assert _cart_test(monkeypatch, active_session, "invoice_postpay,stripe") == "invoice_postpay,stripe"


def test_cart_view_passes_all_three_methods(monkeypatch, active_session):
    assert _cart_test(monkeypatch, active_session, "invoice_prepay,invoice_postpay,stripe") == "invoice_prepay,invoice_postpay,stripe"


def test_cart_view_defaults_invoice_prepay_when_missing(monkeypatch, active_session):
    assert _cart_test(monkeypatch, active_session, None) == "invoice_prepay"


# -- admin update form --------------------------------------------------------

def _admin_update_test(monkeypatch, active_session, form_data):
    from fastapi.testclient import TestClient
    updated: dict = {}
    async def fake_super(request): return {"id": 1}, None
    async def fake_get(company_id): return {"id": company_id, "name": "N", "payment_method": "invoice_prepay"}
    def fake_parse(text): return []
    async def fake_update(company_id, **kwargs): updated.update(kwargs)
    monkeypatch.setattr(main, "_require_super_admin_page", fake_super)
    monkeypatch.setattr(main.company_repo, "get_company_by_id", fake_get)
    monkeypatch.setattr(main.company_domains, "parse_email_domain_text", fake_parse)
    monkeypatch.setattr(main.company_repo, "update_company", fake_update)
    with TestClient(main.app, follow_redirects=False) as client:
        r = client.post("/admin/companies/1", data={"name": "TC", "_csrf": active_session.csrf_token, **form_data})
    assert r.status_code == 303
    return updated.get("payment_method")


def test_admin_saves_invoice_prepay_only(monkeypatch, active_session):
    assert _admin_update_test(monkeypatch, active_session, {"invoicePrepay": "1"}) == "invoice_prepay"


def test_admin_saves_invoice_postpay_only(monkeypatch, active_session):
    assert _admin_update_test(monkeypatch, active_session, {"invoicePostpay": "1"}) == "invoice_postpay"


def test_admin_saves_stripe_only(monkeypatch, active_session):
    assert _admin_update_test(monkeypatch, active_session, {"stripeEnabled": "1"}) == "stripe"


def test_admin_saves_invoice_prepay_and_stripe(monkeypatch, active_session):
    assert _admin_update_test(monkeypatch, active_session, {"invoicePrepay": "1", "stripeEnabled": "1"}) == "invoice_prepay,stripe"


def test_admin_saves_invoice_postpay_and_stripe(monkeypatch, active_session):
    assert _admin_update_test(monkeypatch, active_session, {"invoicePostpay": "1", "stripeEnabled": "1"}) == "invoice_postpay,stripe"


def test_admin_saves_all_three_methods(monkeypatch, active_session):
    result = _admin_update_test(monkeypatch, active_session, {"invoicePrepay": "1", "invoicePostpay": "1", "stripeEnabled": "1"})
    assert result == "invoice_prepay,invoice_postpay,stripe"


def test_admin_defaults_invoice_prepay_when_none_selected(monkeypatch, active_session):
    assert _admin_update_test(monkeypatch, active_session, {}) == "invoice_prepay"
