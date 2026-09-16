"""Tests for subscription_renewals service."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.services import subscription_renewals as renewals_service
from app.repositories import subscriptions as subscriptions_repo
from app.repositories import scheduled_invoices as invoices_repo
from app.repositories import shop as shop_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# Helper: build a minimal subscription dict
def _sub(sub_id, customer_id, end_date, product_id=1, unit_price="10.00", status="active"):
    return {
        "id": sub_id,
        "customer_id": customer_id,
        "end_date": end_date,
        "product_id": product_id,
        "unit_price": unit_price,
        "status": status,
    }


# ---------------------------------------------------------------------------
# create_renewal_invoices_for_date
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_subscriptions_returns_zero_counts(monkeypatch):
    monkeypatch.setattr(
        subscriptions_repo, "list_subscriptions", AsyncMock(return_value=[])
    )

    result = await renewals_service.create_renewal_invoices_for_date(date(2025, 1, 1))

    assert result["processed_count"] == 0
    assert result["invoice_count"] == 0
    assert result["customer_count"] == 0
    assert result["skipped_count"] == 0


@pytest.mark.anyio
async def test_creates_invoice_for_single_subscription(monkeypatch):
    target = date(2025, 1, 1)
    renewal_date = target + timedelta(days=60)
    sub = _sub("sub-1", customer_id=10, end_date=renewal_date)

    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=[sub]),
    )
    monkeypatch.setattr(
        invoices_repo,
        "get_scheduled_invoice_by_customer_and_date",
        AsyncMock(return_value=None),
    )
    created_invoice = {"id": "inv-1"}
    monkeypatch.setattr(
        invoices_repo,
        "create_scheduled_invoice",
        AsyncMock(return_value=created_invoice),
    )
    monkeypatch.setattr(
        invoices_repo, "add_invoice_line", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        shop_repo,
        "get_product_by_id",
        AsyncMock(return_value={"id": 1, "commitment_type": "annual"}),
    )
    monkeypatch.setattr(
        subscriptions_repo,
        "update_subscription",
        AsyncMock(return_value=None),
    )

    result = await renewals_service.create_renewal_invoices_for_date(target)

    assert result["invoice_count"] == 1
    assert result["processed_count"] == 1
    assert result["customer_count"] == 1
    assert result["skipped_count"] == 0


@pytest.mark.anyio
async def test_skips_existing_invoice_but_still_marks_renewal(monkeypatch):
    target = date(2025, 1, 1)
    renewal_date = target + timedelta(days=60)
    sub = _sub("sub-2", customer_id=20, end_date=renewal_date)

    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=[sub]),
    )
    monkeypatch.setattr(
        invoices_repo,
        "get_scheduled_invoice_by_customer_and_date",
        AsyncMock(return_value={"id": "existing-inv"}),
    )
    create_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(invoices_repo, "create_scheduled_invoice", create_mock)
    update_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(subscriptions_repo, "update_subscription", update_mock)

    result = await renewals_service.create_renewal_invoices_for_date(target)

    assert result["skipped_count"] == 1
    assert result["invoice_count"] == 0
    create_mock.assert_not_awaited()
    update_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_groups_subscriptions_by_customer(monkeypatch):
    target = date(2025, 1, 1)
    renewal_date = target + timedelta(days=60)

    subs = [
        _sub("sub-a", customer_id=10, end_date=renewal_date),
        _sub("sub-b", customer_id=10, end_date=renewal_date),  # same customer
        _sub("sub-c", customer_id=20, end_date=renewal_date),
    ]

    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=subs),
    )
    monkeypatch.setattr(
        invoices_repo,
        "get_scheduled_invoice_by_customer_and_date",
        AsyncMock(return_value=None),
    )
    invoice_counter = {"count": 0}

    async def fake_create(**kwargs):
        invoice_counter["count"] += 1
        return {"id": f"inv-{invoice_counter['count']}"}

    monkeypatch.setattr(invoices_repo, "create_scheduled_invoice", fake_create)
    monkeypatch.setattr(invoices_repo, "add_invoice_line", AsyncMock(return_value=None))
    monkeypatch.setattr(
        shop_repo,
        "get_product_by_id",
        AsyncMock(return_value={"id": 1, "commitment_type": "monthly"}),
    )
    monkeypatch.setattr(
        subscriptions_repo,
        "update_subscription",
        AsyncMock(return_value=None),
    )

    result = await renewals_service.create_renewal_invoices_for_date(target)

    # Two unique customers → two invoices
    assert result["invoice_count"] == 2
    assert result["customer_count"] == 2
    assert result["processed_count"] == 3


@pytest.mark.anyio
async def test_monthly_commitment_sets_30_day_term(monkeypatch):
    target = date(2025, 1, 1)
    renewal_date = target + timedelta(days=60)
    sub = _sub("sub-m", customer_id=5, end_date=renewal_date)

    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=[sub]),
    )
    monkeypatch.setattr(
        invoices_repo,
        "get_scheduled_invoice_by_customer_and_date",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        invoices_repo,
        "create_scheduled_invoice",
        AsyncMock(return_value={"id": "inv-m"}),
    )
    captured_lines: list[dict] = []

    async def fake_add_line(**kwargs):
        captured_lines.append(kwargs)

    monkeypatch.setattr(invoices_repo, "add_invoice_line", fake_add_line)
    monkeypatch.setattr(
        shop_repo,
        "get_product_by_id",
        AsyncMock(return_value={"id": 1, "commitment_type": "monthly"}),
    )
    monkeypatch.setattr(
        subscriptions_repo,
        "update_subscription",
        AsyncMock(return_value=None),
    )

    await renewals_service.create_renewal_invoices_for_date(target)

    assert len(captured_lines) == 1
    line = captured_lines[0]
    term_days = (line["term_end"] - line["term_start"]).days
    # Monthly → term_days = 30, and end = start + (30 - 1) days, so diff = 29
    assert term_days == 29


@pytest.mark.anyio
async def test_annual_commitment_sets_365_day_term(monkeypatch):
    target = date(2025, 1, 1)
    renewal_date = target + timedelta(days=60)
    sub = _sub("sub-a", customer_id=5, end_date=renewal_date)

    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=[sub]),
    )
    monkeypatch.setattr(
        invoices_repo,
        "get_scheduled_invoice_by_customer_and_date",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        invoices_repo,
        "create_scheduled_invoice",
        AsyncMock(return_value={"id": "inv-a"}),
    )
    captured_lines: list[dict] = []

    async def fake_add_line(**kwargs):
        captured_lines.append(kwargs)

    monkeypatch.setattr(invoices_repo, "add_invoice_line", fake_add_line)
    monkeypatch.setattr(
        shop_repo,
        "get_product_by_id",
        AsyncMock(return_value={"id": 1, "commitment_type": "annual"}),
    )
    monkeypatch.setattr(
        subscriptions_repo,
        "update_subscription",
        AsyncMock(return_value=None),
    )

    await renewals_service.create_renewal_invoices_for_date(target)

    assert len(captured_lines) == 1
    line = captured_lines[0]
    term_days = (line["term_end"] - line["term_start"]).days
    # Annual → term_days = 365, and end = start + (365 - 1) days, so diff = 364
    assert term_days == 364


@pytest.mark.anyio
async def test_unknown_product_commitment_defaults_to_annual(monkeypatch):
    target = date(2025, 1, 1)
    renewal_date = target + timedelta(days=60)
    sub = _sub("sub-x", customer_id=7, end_date=renewal_date)

    monkeypatch.setattr(
        subscriptions_repo,
        "list_subscriptions",
        AsyncMock(return_value=[sub]),
    )
    monkeypatch.setattr(
        invoices_repo,
        "get_scheduled_invoice_by_customer_and_date",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        invoices_repo,
        "create_scheduled_invoice",
        AsyncMock(return_value={"id": "inv-x"}),
    )
    captured_lines: list[dict] = []

    async def fake_add_line(**kwargs):
        captured_lines.append(kwargs)

    monkeypatch.setattr(invoices_repo, "add_invoice_line", fake_add_line)
    # Product not found → None
    monkeypatch.setattr(
        shop_repo, "get_product_by_id", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        subscriptions_repo,
        "update_subscription",
        AsyncMock(return_value=None),
    )

    await renewals_service.create_renewal_invoices_for_date(target)

    assert len(captured_lines) == 1
    line = captured_lines[0]
    term_days = (line["term_end"] - line["term_start"]).days
    # Annual → term_days = 365, and end = start + (365 - 1) days, so diff = 364
    assert term_days == 364  # annual fallback


# ---------------------------------------------------------------------------
# get_next_scheduled_invoice_for_subscription
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_next_invoice_subscription_not_found(monkeypatch):
    monkeypatch.setattr(
        subscriptions_repo, "get_subscription", AsyncMock(return_value=None)
    )

    result = await renewals_service.get_next_scheduled_invoice_for_subscription("sub-999")
    assert result is None


def test_build_renewal_forecast_groups_windows_and_reminders():
    today = date(2026, 1, 1)
    subscriptions = [
        _sub("sub-7", customer_id=10, end_date=today + timedelta(days=7), unit_price="10.00"),
        _sub("sub-30", customer_id=10, end_date=today + timedelta(days=30), unit_price="20.00"),
        _sub("sub-60", customer_id=10, end_date=today + timedelta(days=60), unit_price="30.00"),
        _sub("sub-120", customer_id=10, end_date=today + timedelta(days=120), unit_price="40.00"),
    ]
    subscriptions[0]["quantity"] = 1
    subscriptions[1]["quantity"] = 2
    subscriptions[2]["quantity"] = 3
    subscriptions[3]["quantity"] = 4

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
        subscriptions_repo, "get_subscription", AsyncMock(return_value=sub)
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
        subscriptions_repo, "get_subscription", AsyncMock(return_value=sub)
    )
    monkeypatch.setattr(
        invoices_repo,
        "get_scheduled_invoice_by_customer_and_date",
        AsyncMock(return_value=None),
    )

    result = await renewals_service.get_next_scheduled_invoice_for_subscription("sub-1")
    assert result is None
