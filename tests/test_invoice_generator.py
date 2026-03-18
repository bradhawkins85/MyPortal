"""Tests for the local invoice generator service."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import invoice_generator


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


def _make_company(company_id: int = 1) -> dict[str, Any]:
    return {"id": company_id, "name": "Test Company"}


def _make_recurring_items() -> list[dict[str, Any]]:
    return [
        {
            "Description": "Managed IT support",
            "Quantity": 1.0,
            "UnitAmount": 500.0,
            "ItemCode": "MSP-MANAGED",
        },
        {
            "Description": "Per-device fee",
            "Quantity": 5.0,
            "UnitAmount": 10.0,
            "ItemCode": "MSP-DEVICE",
        },
    ]


# ---------------------------------------------------------------------------
# _generate_invoice_number
# ---------------------------------------------------------------------------


def test_generate_invoice_number_first(monkeypatch):
    """When no invoices exist for the current month, sequence starts at 1."""

    async def fake_get_max_seq(prefix: str) -> int:
        return 0

    monkeypatch.setattr(invoice_generator.invoice_repo, "get_max_invoice_seq", fake_get_max_seq)

    from datetime import datetime, timezone

    fixed_now = datetime(2026, 3, 18, tzinfo=timezone.utc)
    with patch("app.services.invoice_generator.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = asyncio.run(invoice_generator._generate_invoice_number())

    assert result == "INV-202603-0001"


def test_generate_invoice_number_increments(monkeypatch):
    """Sequence number increments beyond the current maximum."""

    async def fake_get_max_seq(prefix: str) -> int:
        return 12

    monkeypatch.setattr(invoice_generator.invoice_repo, "get_max_invoice_seq", fake_get_max_seq)

    from datetime import datetime, timezone

    fixed_now = datetime(2026, 3, 18, tzinfo=timezone.utc)
    with patch("app.services.invoice_generator.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = asyncio.run(invoice_generator._generate_invoice_number())

    assert result == "INV-202603-0013"


# ---------------------------------------------------------------------------
# generate_invoice — company not found
# ---------------------------------------------------------------------------


def test_generate_invoice_company_not_found(monkeypatch):
    async def fake_get_company(company_id):
        return None

    monkeypatch.setattr(invoice_generator.company_repo, "get_company_by_id", fake_get_company)

    result = asyncio.run(invoice_generator.generate_invoice(99))

    assert result["status"] == "skipped"
    assert result["reason"] == "Company not found"
    assert result["company_id"] == 99


# ---------------------------------------------------------------------------
# generate_invoice — no line items → skipped
# ---------------------------------------------------------------------------


def test_generate_invoice_no_line_items(monkeypatch):
    monkeypatch.setattr(
        invoice_generator.company_repo,
        "get_company_by_id",
        AsyncMock(return_value=_make_company()),
    )
    monkeypatch.setattr(
        invoice_generator.xero_service,
        "build_invoice_context",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        invoice_generator.xero_service,
        "build_recurring_invoice_items",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        invoice_generator.tickets_repo,
        "list_tickets",
        AsyncMock(return_value=[]),
    )

    result = asyncio.run(invoice_generator.generate_invoice(1))

    assert result["status"] == "skipped"
    assert "No active recurring invoice items" in result["reason"]


# ---------------------------------------------------------------------------
# generate_invoice — recurring items only, no tickets
# ---------------------------------------------------------------------------


def test_generate_invoice_recurring_items_only(monkeypatch):
    created_invoices: list[dict[str, Any]] = []
    created_lines: list[dict[str, Any]] = []

    async def fake_create_invoice(**kwargs):
        inv = {"id": 101, **kwargs}
        created_invoices.append(inv)
        return inv

    async def fake_create_line(**kwargs):
        line = {"id": len(created_lines) + 1, **kwargs}
        created_lines.append(line)
        return line

    async def fake_get_max_seq(prefix: str) -> int:
        return 0

    monkeypatch.setattr(
        invoice_generator.company_repo,
        "get_company_by_id",
        AsyncMock(return_value=_make_company()),
    )
    monkeypatch.setattr(
        invoice_generator.xero_service,
        "build_invoice_context",
        AsyncMock(return_value={"company_name": "Test Company"}),
    )
    monkeypatch.setattr(
        invoice_generator.xero_service,
        "build_recurring_invoice_items",
        AsyncMock(return_value=_make_recurring_items()),
    )
    monkeypatch.setattr(
        invoice_generator.tickets_repo,
        "list_tickets",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(invoice_generator.invoice_repo, "create_invoice", fake_create_invoice)
    monkeypatch.setattr(invoice_generator.invoice_repo, "get_max_invoice_seq", fake_get_max_seq)
    monkeypatch.setattr(invoice_generator.invoice_lines_repo, "create_invoice_line", fake_create_line)

    from datetime import datetime, timezone

    fixed_now = datetime(2026, 3, 18, tzinfo=timezone.utc)
    with patch("app.services.invoice_generator.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = asyncio.run(invoice_generator.generate_invoice(1))

    assert result["status"] == "succeeded"
    assert result["invoice_number"] == "INV-202603-0001"
    # 1 * 500 + 5 * 10 = 550
    assert result["total_amount"] == "550.00"
    assert result["line_items"] == 2
    assert result["recurring_items"] == 2
    assert result["ticket_items"] == 0
    assert result["tickets_billed"] == 0
    assert len(created_invoices) == 1
    assert len(created_lines) == 2
    assert created_invoices[0]["status"] == "draft"
    assert created_invoices[0]["company_id"] == 1


# ---------------------------------------------------------------------------
# get_max_invoice_seq helper in repository
# ---------------------------------------------------------------------------


def test_get_max_invoice_seq_returns_zero_for_none(monkeypatch):
    """When the DB returns a row with max_seq=None, the function returns 0."""
    import asyncio

    from app.repositories import invoices as inv_repo

    async def fake_fetch_one(query, params=None):
        return {"max_seq": None}

    monkeypatch.setattr(inv_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(inv_repo.get_max_invoice_seq("INV-202603-"))
    assert result == 0


def test_get_max_invoice_seq_returns_value(monkeypatch):
    """When the DB returns a row with max_seq=7, the function returns 7."""
    import asyncio

    from app.repositories import invoices as inv_repo

    async def fake_fetch_one(query, params=None):
        return {"max_seq": 7}

    monkeypatch.setattr(inv_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(inv_repo.get_max_invoice_seq("INV-202603-"))
    assert result == 7
