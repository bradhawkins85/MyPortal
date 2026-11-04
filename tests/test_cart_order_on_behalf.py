"""Technicians can place a cart order on behalf of a customer staff member."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.responses import HTMLResponse

from app import main
from tests.test_cart_shipping_address import (  # noqa: F401 - fixtures
    _place_order_test,
    active_session,
    anyio_backend,
    mock_startup,
)

_STAFF = [
    {"id": 7, "first_name": "Jamie", "last_name": "Customer", "email": "jamie@example.com"},
    {"id": 8, "first_name": "Other", "last_name": "Domain", "email": "other@elsewhere.test"},
]


@pytest.fixture
def staff_choices(monkeypatch):
    async def fake_company(company_id):
        return {"id": company_id, "email_domains": ["example.com"]}

    async def fake_list_staff(company_id, *, exclude_staff_id=None):
        return [dict(member) for member in _STAFF]

    async def fake_user_by_email(email):
        return {"id": 55, "email": email} if email == "jamie@example.com" else None

    audits: list[dict[str, Any]] = []

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(main.company_repo, "get_company_by_id", fake_company)
    monkeypatch.setattr(main.staff_repo, "list_active_staff_for_offboarding", fake_list_staff)
    monkeypatch.setattr(main.user_repo, "get_user_by_email", fake_user_by_email)
    monkeypatch.setattr(main.audit_service, "log_action", fake_audit)
    return audits


def _technician(monkeypatch, allowed: bool) -> None:
    async def fake_access(user, request=None):
        return allowed

    monkeypatch.setattr(main, "_has_admin_technician_access", fake_access)


def test_technician_places_order_on_behalf_of_staff(monkeypatch, active_session, staff_choices):
    _technician(monkeypatch, True)
    orders: list = []
    tickets: list = []
    invoices: list = []
    location = _place_order_test(
        monkeypatch,
        active_session,
        form_data={"shippingOption": "local_pickup", "requestedOnBehalfOfStaffId": "7"},
        capture_create_order_kwargs=orders,
        capture_create_ticket_kwargs=tickets,
        capture_invoice_kwargs=invoices,
    )
    assert "/cart/order-placed" in location
    assert orders[0]["requested_by_staff_id"] == 7
    assert orders[0]["requested_by_name"] == "Jamie Customer"
    assert orders[0]["requested_by_email"] == "jamie@example.com"
    assert tickets[0]["requester_staff_id"] == 7
    assert tickets[0]["requester_id"] == 55
    assert tickets[0]["initial_reply_author_id"] == 10
    assert "Jamie Customer (jamie@example.com) (placed on their behalf)" in tickets[0]["description"]
    assert "Placed by: Test User" in tickets[0]["description"]
    assert invoices[0]["user_name"] == "Jamie Customer"
    assert staff_choices[0]["action"] == "shop.order.placed_on_behalf"
    assert staff_choices[0]["metadata"]["requested_by_staff_id"] == 7


def test_order_without_selection_records_placing_user(monkeypatch, active_session, staff_choices):
    _technician(monkeypatch, True)
    orders: list = []
    tickets: list = []
    _place_order_test(
        monkeypatch,
        active_session,
        form_data={"shippingOption": "local_pickup"},
        capture_create_order_kwargs=orders,
        capture_create_ticket_kwargs=tickets,
    )
    assert orders[0]["requested_by_staff_id"] is None
    assert orders[0]["requested_by_name"] == "Test User"
    assert orders[0]["requested_by_email"] == "test@example.com"
    assert tickets[0]["requester_id"] == 10
    assert tickets[0]["requester_staff_id"] is None
    assert not staff_choices


def test_staff_outside_company_choices_is_rejected(monkeypatch, active_session, staff_choices):
    _technician(monkeypatch, True)
    orders: list = []
    for staff_id in ("8", "999", "abc"):
        location = _place_order_test(
            monkeypatch,
            active_session,
            form_data={"shippingOption": "local_pickup", "requestedOnBehalfOfStaffId": staff_id},
            capture_create_order_kwargs=orders,
        )
        assert "orderMessage" in location
    assert not orders


def test_non_technician_cannot_order_on_behalf(monkeypatch, active_session, staff_choices):
    _technician(monkeypatch, False)
    orders: list = []
    # The route answers 403 rather than the 303 redirect the helper expects.
    with pytest.raises(AssertionError):
        _place_order_test(
            monkeypatch,
            active_session,
            form_data={"shippingOption": "local_pickup", "requestedOnBehalfOfStaffId": "7"},
            capture_create_order_kwargs=orders,
        )
    assert not orders


def _cart_extra(monkeypatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_ctx(request, *, permission_field):
        company = {"id": 1, "name": "E", "is_vip": 0, "payment_method": "invoice_prepay", "require_po": 0}
        return ({"id": 10}, {"company_id": 1, "can_access_cart": True}, company, 1, None)

    async def fake_list_items(session_id):
        return []

    async def fake_render(template_name, request_obj, user_obj, *, extra):
        captured.update(extra)
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_load_company_section_context", fake_ctx)
    monkeypatch.setattr(main.cart_repo, "list_items", fake_list_items)
    monkeypatch.setattr(main, "_render_template", fake_render)
    with TestClient(main.app, follow_redirects=False) as client:
        client.get("/cart")
    return captured


def test_cart_lists_requester_choices_for_technicians(monkeypatch, active_session, staff_choices):
    _technician(monkeypatch, True)
    extra = _cart_extra(monkeypatch)
    assert extra["can_order_on_behalf"] is True
    assert [member["id"] for member in extra["order_requester_options"]] == [7]


def test_cart_hides_requester_choices_for_customers(monkeypatch, active_session, staff_choices):
    _technician(monkeypatch, False)
    extra = _cart_extra(monkeypatch)
    assert extra["can_order_on_behalf"] is False
    assert extra["order_requester_options"] == []


def test_migration_adds_requested_by_columns():
    sql = (Path(__file__).parent.parent / "migrations" / "540_shop_orders_requested_by.sql").read_text()
    assert "-- phase: expand" in sql
    for column in ("requested_by_staff_id", "requested_by_name", "requested_by_email"):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql
