"""Tests for subscription_renewals service."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.repositories import scheduled_invoices as invoices_repo
from app.repositories import subscriptions as subscriptions_repo
from app.services import subscription_renewals as renewals_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _sub(
    sub_id: str,
    customer_id: int,
    end_date: date,
    *,
    product_id: int = 1,
    quantity: int = 10,
    unit_price: str = "20.00",
    status: str = "active",
    auto_renew: bool = True,
    product_name: str = "Managed Plan",
) -> dict[str, object]:
    return {
        "id": sub_id,
        "customer_id": customer_id,
        "end_date": end_date,
        "product_id": product_id,
        "product_name": product_name,
        "unit_price": Decimal(unit_price),
        "quantity": quantity,
        "status": status,
        "auto_renew": auto_renew,
    }


def _scheduled_invoice_state(*, customer_id: int, renewal_date: date) -> dict[str, object]:
    return {
        "id": 44,
        "customer_id": customer_id,
        "scheduled_for_date": renewal_date,
        "status": "scheduled",
        "reminder_ticket_id": None,
        "reminder_reply_id": None,
        "reminder_sent_at": None,
        "reminder_email_sent_at": None,
        "reminder_error": None,
        "invoice_id": None,
        "invoice_number": None,
        "invoice_sent_at": None,
        "invoice_error": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


def _install_scheduled_invoice_mocks(monkeypatch, state: dict[str, object] | None):
    stored = state
    line_calls: list[dict[str, object]] = []
    patch_calls: list[dict[str, object]] = []

    async def fake_get_by_customer_and_date(_customer_id, _scheduled_date):
        return dict(stored) if stored is not None else None

    async def fake_create_scheduled_invoice(*, customer_id, scheduled_for_date, status):
        nonlocal stored
        stored = _scheduled_invoice_state(
            customer_id=customer_id,
            renewal_date=scheduled_for_date,
        )
        stored["status"] = status
        return dict(stored)

    async def fake_patch(invoice_id, **updates):
        assert stored is not None
        assert invoice_id == stored["id"]
        stored.update(updates)
        patch_calls.append(dict(updates))
        return dict(stored)

    async def fake_get_scheduled_invoice(invoice_id):
        assert stored is not None
        assert invoice_id == stored["id"]
        return dict(stored)

    async def fake_delete_lines(invoice_id):
        assert stored is not None
        assert invoice_id == stored["id"]
        line_calls.clear()

    async def fake_add_line(**kwargs):
        line_calls.append(dict(kwargs))

    monkeypatch.setattr(
        invoices_repo,
        "get_scheduled_invoice_by_customer_and_date",
        fake_get_by_customer_and_date,
    )
    monkeypatch.setattr(
        invoices_repo,
        "create_scheduled_invoice",
        fake_create_scheduled_invoice,
    )
    monkeypatch.setattr(invoices_repo, "patch_scheduled_invoice", fake_patch)
    monkeypatch.setattr(invoices_repo, "get_scheduled_invoice", fake_get_scheduled_invoice)
    monkeypatch.setattr(invoices_repo, "delete_invoice_lines", fake_delete_lines)
    monkeypatch.setattr(invoices_repo, "add_invoice_line", fake_add_line)
    return patch_calls, line_calls, lambda: stored


@pytest.mark.anyio
async def test_no_subscriptions_returns_zero_counts(monkeypatch):
    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=[]),
    )

    result = await renewals_service.create_renewal_invoices_for_date(date(2025, 1, 1))

    assert result == {
        "processed_count": 0,
        "reminder_count": 0,
        "invoice_count": 0,
        "customer_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "processed_subscription_ids": [],
        "issues": [],
    }


@pytest.mark.anyio
async def test_creates_60_day_reminder_with_pending_decrease_details(monkeypatch):
    target = date(2025, 1, 1)
    renewal_date = target + timedelta(days=60)
    subscription = _sub("sub-1", customer_id=22, end_date=renewal_date)
    patch_calls, line_calls, get_state = _install_scheduled_invoice_mocks(
        monkeypatch,
        None,
    )
    create_ticket_calls: list[dict[str, object]] = []
    create_reply_calls: list[dict[str, object]] = []
    email_calls: list[dict[str, object]] = []
    update_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=[subscription]),
    )
    monkeypatch.setattr(
        renewals_service.change_requests_repo,
        "list_pending_changes_for_subscriptions",
        AsyncMock(
            return_value={
                "sub-1": [
                    {
                        "change_type": "decrease",
                        "quantity_change": 5,
                        "status": "pending",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(
        renewals_service.shop_repo,
        "get_product_by_id",
        AsyncMock(
            return_value={
                "id": 1,
                "name": "Managed Plan",
                "sku": "SKU-1",
                "vendor_sku": "SKU-1",
                "invoice_description": "Managed Plan annual renewal",
                "commitment_type": "annual",
                "payment_frequency": "annual",
            }
        ),
    )
    monkeypatch.setattr(
        renewals_service.license_repo,
        "list_company_licenses",
        AsyncMock(
            return_value=[
                {"id": 7, "platform": "SKU-1", "display_name": "Managed License"}
            ]
        ),
    )
    monkeypatch.setattr(
        renewals_service.license_repo,
        "list_staff_by_license_for_company",
        AsyncMock(
            return_value={
                7: [
                    {
                        "id": 1,
                        "first_name": "Alex",
                        "last_name": "Example",
                        "email": "alex@example.com",
                    },
                    {
                        "id": 2,
                        "first_name": "Jamie",
                        "last_name": "Example",
                        "email": "jamie@example.com",
                    },
                ]
            }
        ),
    )
    monkeypatch.setattr(
        renewals_service.company_repo,
        "get_company_by_id",
        AsyncMock(
            return_value={
                "id": 22,
                "name": "Acme Pty Ltd",
                "xero_auto_send_subscription_invoices": 1,
            }
        ),
    )
    monkeypatch.setattr(
        renewals_service.billing_contacts_repo,
        "list_billing_contacts_for_company",
        AsyncMock(
            return_value=[
                {
                    "staff_id": 101,
                    "email": "billing@example.com",
                    "first_name": "Bill",
                    "last_name": "To",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        renewals_service.tickets_repo,
        "get_ticket_by_external_reference",
        AsyncMock(return_value=None),
    )

    async def fake_create_ticket(**kwargs):
        create_ticket_calls.append(dict(kwargs))
        return {"id": 88, **kwargs}

    async def fake_create_reply(**kwargs):
        create_reply_calls.append(dict(kwargs))
        return {"id": 99, **kwargs}

    async def fake_send_email(**kwargs):
        email_calls.append(dict(kwargs))
        return True, None

    async def fake_update_subscription(subscription_id, **kwargs):
        update_calls.append({"subscription_id": subscription_id, **kwargs})

    monkeypatch.setattr(renewals_service.tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(
        renewals_service.tickets_repo,
        "get_reply_by_external_reference",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(renewals_service.tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(renewals_service.email_service, "send_email", fake_send_email)
    monkeypatch.setattr(subscriptions_repo, "update_subscription", fake_update_subscription)
    monkeypatch.setattr(
        renewals_service.invoice_generator,
        "generate_invoice",
        AsyncMock(),
    )

    result = await renewals_service.create_renewal_invoices_for_date(target)

    assert result["processed_count"] == 1
    assert result["reminder_count"] == 1
    assert result["invoice_count"] == 0
    assert result["error_count"] == 0
    assert create_ticket_calls[0]["requester_id"] == 101
    assert create_ticket_calls[0]["company_id"] == 22
    assert create_ticket_calls[0]["module_slug"] == "subscriptions"
    assert "Renewing quantity: 5" in create_ticket_calls[0]["description"]
    assert "Unassigned licenses: 3" in create_ticket_calls[0]["description"]
    assert "Default billing currency 100.00" in create_ticket_calls[0]["description"]
    assert "Alex Example <alex@example.com>" in create_reply_calls[0]["body"]
    assert email_calls[0]["recipients"] == ["billing@example.com"]
    assert "Renewing quantity: 5" in email_calls[0]["text_body"]
    assert line_calls[0]["price"] == Decimal("20.00")
    assert line_calls[0]["term_end"] - line_calls[0]["term_start"] == timedelta(days=364)
    assert update_calls == [{"subscription_id": "sub-1", "status": "pending_renewal"}]
    state = get_state()
    assert state is not None
    assert state["reminder_ticket_id"] == 88
    assert state["reminder_reply_id"] == 99
    assert state["reminder_sent_at"] is not None
    assert state["reminder_email_sent_at"] is not None
    assert patch_calls


@pytest.mark.anyio
async def test_generates_30_day_invoice_with_current_renewal_quantity(monkeypatch):
    target = date(2025, 2, 1)
    renewal_date = target + timedelta(days=30)
    subscription = _sub("sub-2", customer_id=31, end_date=renewal_date)
    existing = _scheduled_invoice_state(customer_id=31, renewal_date=renewal_date)
    existing["reminder_ticket_id"] = 88
    existing["reminder_reply_id"] = 99
    existing["reminder_sent_at"] = datetime.now(timezone.utc)
    existing["reminder_email_sent_at"] = datetime.now(timezone.utc)
    patch_calls, line_calls, get_state = _install_scheduled_invoice_mocks(
        monkeypatch,
        existing,
    )
    invoice_calls: list[dict[str, object]] = []
    sync_calls: list[tuple[int, bool]] = []

    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=[subscription]),
    )
    monkeypatch.setattr(
        renewals_service.change_requests_repo,
        "list_pending_changes_for_subscriptions",
        AsyncMock(
            return_value={
                "sub-2": [
                    {
                        "change_type": "decrease",
                        "quantity_change": 5,
                        "status": "pending",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(
        renewals_service.shop_repo,
        "get_product_by_id",
        AsyncMock(
            return_value={
                "id": 1,
                "name": "Managed Plan",
                "sku": "SKU-1",
                "vendor_sku": "SKU-1",
                "commitment_type": "annual",
                "payment_frequency": "annual",
            }
        ),
    )
    monkeypatch.setattr(
        renewals_service.license_repo,
        "list_company_licenses",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        renewals_service.license_repo,
        "list_staff_by_license_for_company",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        renewals_service.billing_contacts_repo,
        "list_billing_contacts_for_company",
        AsyncMock(
            return_value=[
                {
                    "staff_id": 101,
                    "email": "billing@example.com",
                    "first_name": "Bill",
                    "last_name": "To",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        renewals_service.company_repo,
        "get_company_by_id",
        AsyncMock(
            return_value={
                "id": 31,
                "name": "Example Co",
                "xero_auto_send_subscription_invoices": 0,
            }
        ),
    )
    monkeypatch.setattr(subscriptions_repo, "update_subscription", AsyncMock(return_value=None))

    async def fake_generate_invoice(company_id, **kwargs):
        invoice_calls.append({"company_id": company_id, **kwargs})
        return {"status": "succeeded", "invoice_id": 71, "invoice_number": "INV-71"}

    async def fake_sync_invoice(invoice_id, auto_send=False):
        sync_calls.append((invoice_id, auto_send))
        return {"status": "succeeded", "invoice_id": invoice_id}

    monkeypatch.setattr(
        renewals_service.invoice_generator,
        "generate_invoice",
        fake_generate_invoice,
    )
    monkeypatch.setattr(renewals_service.xero_service, "sync_invoice", fake_sync_invoice)

    result = await renewals_service.create_renewal_invoices_for_date(target)

    assert result["invoice_count"] == 1
    assert invoice_calls[0]["company_id"] == 31
    assert invoice_calls[0]["include_recurring_items"] is False
    assert invoice_calls[0]["include_ticket_items"] is False
    assert invoice_calls[0]["recurring_line_items"] == [
        {
            "Description": "Managed Plan renewal - Annual commitment · annual payment (renews 2025-03-03)",
            "Quantity": 5,
            "UnitAmount": 20.0,
            "ItemCode": "SKU-1",
        }
    ]
    assert sync_calls == [(71, False)]
    assert line_calls[0]["price"] == Decimal("20.00")
    state = get_state()
    assert state is not None
    assert state["status"] == "issued"
    assert state["invoice_id"] == 71
    assert state["invoice_number"] == "INV-71"
    assert state["invoice_sent_at"] is not None
    assert any(call.get("invoice_id") == 71 for call in patch_calls)


@pytest.mark.anyio
async def test_skips_duplicate_reminders_and_invoices(monkeypatch):
    target = date(2025, 2, 1)
    renewal_date = target + timedelta(days=30)
    subscription = _sub("sub-3", customer_id=41, end_date=renewal_date, status="pending_renewal")
    existing = _scheduled_invoice_state(customer_id=41, renewal_date=renewal_date)
    existing["reminder_ticket_id"] = 88
    existing["reminder_reply_id"] = 99
    existing["reminder_sent_at"] = datetime.now(timezone.utc)
    existing["reminder_email_sent_at"] = datetime.now(timezone.utc)
    existing["invoice_id"] = 71
    existing["invoice_number"] = "INV-71"
    existing["invoice_sent_at"] = datetime.now(timezone.utc)
    existing["status"] = "issued"
    _patch_calls, _line_calls, _get_state = _install_scheduled_invoice_mocks(
        monkeypatch,
        existing,
    )

    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=[subscription]),
    )
    monkeypatch.setattr(
        renewals_service.change_requests_repo,
        "list_pending_changes_for_subscriptions",
        AsyncMock(return_value={"sub-3": []}),
    )
    monkeypatch.setattr(
        renewals_service.shop_repo,
        "get_product_by_id",
        AsyncMock(
            return_value={
                "id": 1,
                "name": "Managed Plan",
                "sku": "SKU-1",
                "vendor_sku": "SKU-1",
                "commitment_type": "annual",
                "payment_frequency": "annual",
            }
        ),
    )
    monkeypatch.setattr(
        renewals_service.license_repo,
        "list_company_licenses",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        renewals_service.license_repo,
        "list_staff_by_license_for_company",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(subscriptions_repo, "update_subscription", AsyncMock(return_value=None))
    generate_mock = AsyncMock()
    create_ticket_mock = AsyncMock()
    monkeypatch.setattr(renewals_service.invoice_generator, "generate_invoice", generate_mock)
    monkeypatch.setattr(renewals_service.tickets_service, "create_ticket", create_ticket_mock)

    result = await renewals_service.create_renewal_invoices_for_date(target)

    assert result["reminder_count"] == 0
    assert result["invoice_count"] == 0
    assert result["skipped_count"] == 1
    create_ticket_mock.assert_not_awaited()
    generate_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_records_invalid_billing_email_for_staff_follow_up(monkeypatch):
    target = date(2025, 1, 1)
    renewal_date = target + timedelta(days=60)
    subscription = _sub("sub-4", customer_id=51, end_date=renewal_date, quantity=5)
    _patch_calls, _line_calls, get_state = _install_scheduled_invoice_mocks(
        monkeypatch,
        None,
    )
    create_ticket_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=[subscription]),
    )
    monkeypatch.setattr(
        renewals_service.change_requests_repo,
        "list_pending_changes_for_subscriptions",
        AsyncMock(return_value={"sub-4": []}),
    )
    monkeypatch.setattr(
        renewals_service.shop_repo,
        "get_product_by_id",
        AsyncMock(
            return_value={
                "id": 1,
                "name": "Managed Plan",
                "sku": "SKU-1",
                "vendor_sku": "SKU-1",
                "commitment_type": "annual",
                "payment_frequency": "annual",
            }
        ),
    )
    monkeypatch.setattr(
        renewals_service.license_repo,
        "list_company_licenses",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        renewals_service.license_repo,
        "list_staff_by_license_for_company",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        renewals_service.company_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 51, "name": "Contoso", "xero_auto_send_subscription_invoices": 1}),
    )
    monkeypatch.setattr(
        renewals_service.billing_contacts_repo,
        "list_billing_contacts_for_company",
        AsyncMock(
            return_value=[
                {
                    "staff_id": 101,
                    "email": "not-an-email",
                    "first_name": "Invalid",
                    "last_name": "Contact",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        renewals_service.tickets_repo,
        "get_ticket_by_external_reference",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        renewals_service.tickets_repo,
        "get_reply_by_external_reference",
        AsyncMock(return_value=None),
    )

    async def fake_create_ticket(**kwargs):
        create_ticket_calls.append(dict(kwargs))
        return {"id": 88, **kwargs}

    monkeypatch.setattr(renewals_service.tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(
        renewals_service.tickets_repo,
        "create_reply",
        AsyncMock(return_value={"id": 99}),
    )
    email_mock = AsyncMock()
    monkeypatch.setattr(renewals_service.email_service, "send_email", email_mock)
    monkeypatch.setattr(subscriptions_repo, "update_subscription", AsyncMock(return_value=None))

    result = await renewals_service.create_renewal_invoices_for_date(target)

    assert result["reminder_count"] == 1
    assert result["error_count"] == 1
    assert result["issues"][0]["stage"] == "reminder"
    assert "invalid email address" in result["issues"][0]["message"].lower()
    assert create_ticket_calls[0]["requester_id"] == 101
    email_mock.assert_not_awaited()
    state = get_state()
    assert state is not None
    assert "invalid email address" in str(state["reminder_error"]).lower()


@pytest.mark.anyio
async def test_excludes_cancelled_or_non_renewing_subscriptions(monkeypatch):
    target = date(2025, 1, 1)
    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(
            return_value=[
                _sub("sub-cancelled", 1, target + timedelta(days=60), status="canceled"),
                _sub("sub-manual", 1, target + timedelta(days=30), auto_renew=False),
            ]
        ),
    )

    result = await renewals_service.create_renewal_invoices_for_date(target)

    assert result["processed_count"] == 0
    assert result["customer_count"] == 0


@pytest.mark.anyio
async def test_get_next_invoice_subscription_not_found(monkeypatch):
    monkeypatch.setattr(
        subscriptions_repo,
        "get_subscription",
        AsyncMock(return_value=None),
    )

    result = await renewals_service.get_next_scheduled_invoice_for_subscription("sub-999")
    assert result is None


def test_build_renewal_forecast_groups_windows_and_reminders():
    today = date(2026, 1, 1)
    subscriptions = [
        _sub("sub-7", customer_id=10, end_date=today + timedelta(days=7), unit_price="10.00", quantity=1),
        _sub("sub-30", customer_id=10, end_date=today + timedelta(days=30), unit_price="20.00", quantity=2),
        _sub("sub-60", customer_id=10, end_date=today + timedelta(days=60), unit_price="30.00", quantity=3),
        _sub("sub-120", customer_id=10, end_date=today + timedelta(days=120), unit_price="40.00", quantity=4),
    ]

    result = renewals_service.build_renewal_forecast(
        subscriptions,
        today=today,
        reminder_offsets=(60, 30, 7),
    )

    assert result["renewing_in_30_days"] == 2
    assert result["renewing_in_60_days"] == 3
    assert result["renewing_in_90_days"] == 3
    assert result["projected_revenue_90"] == Decimal("140.00")
    assert result["reminder_campaigns"] == [
        {"days_before": 60, "subscription_count": 1, "subscription_ids": ["sub-60"]},
        {"days_before": 30, "subscription_count": 1, "subscription_ids": ["sub-30"]},
        {"days_before": 7, "subscription_count": 1, "subscription_ids": ["sub-7"]},
    ]


def test_build_churn_risk_report_returns_auditable_reasons():
    today = date(2026, 1, 1)
    subscriptions = [
        {
            "id": "sub-risk",
            "product_name": "Managed Plan",
            "end_date": today + timedelta(days=14),
            "status": "active",
            "auto_renew": False,
            "usage_ratio": "0.42",
            "open_ticket_count": 4,
            "payment_health": "overdue",
        }
    ]
    pending_changes = {
        "sub-risk": [
            {"change_type": "decrease", "quantity_change": 2},
        ]
    }

    result = renewals_service.build_churn_risk_report(
        subscriptions,
        today=today,
        pending_changes_by_subscription=pending_changes,
    )

    assert result["high"] == 1
    assert result["total_at_risk"] == 1
    assert result["items"][0]["level"] == "high"
    assert "Auto-renew is disabled inside the 30-day renewal window." in result["items"][0]["reasons"]
    assert "Pending decrease requests indicate a planned contraction." in result["items"][0]["reasons"]
    assert result["items"][0]["evidence"]["payment_health"] == "overdue"


@pytest.mark.anyio
async def test_get_next_invoice_returns_existing_invoice(monkeypatch):
    renewal_date = date(2025, 3, 1)
    sub = _sub("sub-1", customer_id=10, end_date=renewal_date)

    monkeypatch.setattr(
        subscriptions_repo,
        "get_subscription",
        AsyncMock(return_value=sub),
    )
    invoice = {"id": "inv-42", "customer_id": 10}
    monkeypatch.setattr(
        invoices_repo,
        "get_scheduled_invoice_by_customer_and_date",
        AsyncMock(return_value=invoice),
    )

    result = await renewals_service.get_next_scheduled_invoice_for_subscription("sub-1")
    assert result is not None
    assert result["id"] == "inv-42"


@pytest.mark.anyio
async def test_get_next_invoice_no_invoice_returns_none(monkeypatch):
    renewal_date = date(2025, 3, 1)
    sub = _sub("sub-1", customer_id=10, end_date=renewal_date)

    monkeypatch.setattr(
        subscriptions_repo,
        "get_subscription",
        AsyncMock(return_value=sub),
    )
    monkeypatch.setattr(
        invoices_repo,
        "get_scheduled_invoice_by_customer_and_date",
        AsyncMock(return_value=None),
    )

    result = await renewals_service.get_next_scheduled_invoice_for_subscription("sub-1")
    assert result is None
