import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import invoice_generator
from app.services import xero as xero_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_preview_invoice_sync_includes_adjustment_and_contact_lookup():
    invoice = {
        "id": 7,
        "company_id": 1,
        "invoice_number": "INV-7",
        "amount": Decimal("90.00"),
        "due_date": date(2026, 9, 30),
        "created_at": datetime(2026, 9, 16, tzinfo=timezone.utc),
        "approval_required": True,
        "approved_at": None,
        "approved_by": None,
        "billing_adjustment_type": "credit",
        "billing_adjustment_amount": Decimal("-10.00"),
        "billing_adjustment_reason": "SLA credit",
    }
    company = {"id": 1, "name": "Acme", "xero_id": ""}
    invoice_lines = [
        {
            "id": 1,
            "description": "Managed services",
            "quantity": Decimal("1"),
            "unit_amount": Decimal("100.00"),
            "amount": Decimal("100.00"),
            "product_code": "MSP",
        }
    ]

    with patch("app.services.xero.invoice_repo") as mock_invoice_repo, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.invoice_lines_repo") as mock_invoice_lines_repo, \
         patch("app.services.xero.modules_service") as mock_modules, \
         patch("app.services.xero._resolve_company_contact_payload") as mock_contact_lookup:
        mock_invoice_repo.get_invoice_by_id = AsyncMock(return_value=invoice)
        mock_company_repo.get_company_by_id = AsyncMock(return_value=company)
        mock_invoice_lines_repo.list_invoice_lines = AsyncMock(return_value=invoice_lines)
        mock_modules.get_module = AsyncMock(return_value={"settings": {"account_code": "400"}})
        mock_modules.get_xero_credentials = AsyncMock(return_value={"tenant_id": "tenant-1"})
        mock_modules.acquire_xero_access_token = AsyncMock(return_value="token-1")
        mock_contact_lookup.return_value = (
            {"Name": "Acme", "ContactID": "contact-1"},
            {"company_id": 1, "status": "resolved", "contact_id": "contact-1", "contact_name": "Acme"},
        )

        result = await xero_service.preview_invoice_sync(7)

    assert result["status"] == "ready"
    assert result["contact_lookup"]["status"] == "resolved"
    assert result["base_amount"] == "100.00"
    assert result["total_amount"] == "90.00"
    assert result["payload"]["LineItems"][-1]["Description"] == "Credit: SLA credit"
    assert result["payload"]["LineItems"][-1]["UnitAmount"] == -10.0


@pytest.mark.anyio
async def test_sync_company_skips_invoice_until_approved():
    module_settings = {
        "enabled": True,
        "settings": {
            "client_id": "client",
            "client_secret": "secret",
            "tenant_id": "tenant-1",
            "account_code": "400",
        },
    }
    invoices = [
        {
            "id": 11,
            "company_id": 1,
            "invoice_number": "INV-11",
            "approval_required": True,
            "approved_at": None,
            "xero_invoice_id": "",
        }
    ]

    with patch("app.services.xero.modules_service") as mock_modules, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.invoice_repo") as mock_invoice_repo:
        mock_modules.get_module = AsyncMock(return_value=module_settings)
        mock_modules.get_xero_credentials = AsyncMock(return_value={"refresh_token": "refresh-token"})
        mock_modules.acquire_xero_access_token = AsyncMock(return_value="token-1")
        mock_company_repo.get_company_by_id = AsyncMock(return_value={"id": 1, "name": "Acme", "xero_id": "contact-1"})
        mock_invoice_repo.list_unsynced_company_invoices = AsyncMock(return_value=invoices)
        mock_invoice_repo.patch_invoice = AsyncMock()

        result = await xero_service.sync_company(company_id=1)

    assert result["status"] == "skipped"
    assert result["skipped_count"] == 1
    assert result["skipped_invoices"][0]["reason"] == "Invoice requires approval before Xero sync"
    mock_invoice_repo.patch_invoice.assert_awaited_once()
    assert mock_invoice_repo.patch_invoice.await_args.kwargs["xero_sync_error"] == "Invoice requires approval before Xero sync"


@pytest.mark.anyio
async def test_resolve_company_contact_payload_persists_lookup(monkeypatch):
    update_company = AsyncMock()
    monkeypatch.setattr(xero_service.company_repo, "update_company", update_company)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"Contacts": [{"ContactID": "contact-42"}]}

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    class MockAsyncClient:
        async def __aenter__(self):
            return mock_client

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(xero_service.httpx, "AsyncClient", lambda timeout=30.0: MockAsyncClient())

    contact_payload, lookup = await xero_service._resolve_company_contact_payload(
        {"id": 1, "name": "Acme", "xero_id": ""},
        1,
        tenant_id="tenant-1",
        access_token="token-1",
        persist_lookup=True,
    )

    assert contact_payload["ContactID"] == "contact-42"
    assert lookup["status"] == "resolved"
    update_company.assert_awaited_once_with(1, xero_id="contact-42")


def test_generate_invoice_supports_manual_bill_now_filters_and_approval(monkeypatch):
    monkeypatch.setenv("XERO_BILLABLE_STATUSES", "resolved")

    async def fake_company(company_id: int):
        return {"id": company_id, "name": "Acme"}

    async def fake_context(company_id: int):
        return {}

    async def fake_rate_lookup():
        return None, None

    recurring_builder = AsyncMock(return_value=[])

    async def fake_list_tickets(company_id: int, limit=None):
        return [
            {"id": 1, "company_id": company_id, "subject": "Ignored", "status": "resolved"},
            {"id": 2, "company_id": company_id, "subject": "Included", "status": "resolved"},
        ]

    async def fake_unbilled_reply_ids(ticket_id: int):
        return {ticket_id * 100}

    async def fake_list_expenses(ticket_id: int, unbilled_only=True):
        return []

    async def fake_list_replies(ticket_id: int, include_internal=True):
        return [
            {
                "id": ticket_id * 100,
                "minutes_spent": 60,
                "is_billable": True,
                "labour_type_id": None,
                "labour_type_code": "LAB",
                "labour_type_name": "Labour",
                "labour_type_rate": "1.50",
            }
        ]

    create_invoice = AsyncMock(return_value={"id": 55})
    create_invoice_line = AsyncMock()
    mark_recurring = AsyncMock()
    create_billed_time_entry = AsyncMock()
    update_ticket = AsyncMock()

    monkeypatch.setattr(invoice_generator.company_repo, "get_company_by_id", fake_company)
    monkeypatch.setattr(invoice_generator.xero_service, "build_invoice_context", fake_context)
    monkeypatch.setattr(invoice_generator, "_get_xero_rate_lookup_credentials", fake_rate_lookup)
    monkeypatch.setattr(invoice_generator.xero_service, "build_recurring_invoice_items", recurring_builder)
    monkeypatch.setattr(invoice_generator.tickets_repo, "list_tickets", fake_list_tickets)
    monkeypatch.setattr(invoice_generator.billed_time_repo, "get_unbilled_reply_ids", fake_unbilled_reply_ids)
    monkeypatch.setattr(invoice_generator.expenses_repo, "list_expenses", fake_list_expenses)
    monkeypatch.setattr(invoice_generator.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(invoice_generator.invoice_repo, "get_max_invoice_seq", AsyncMock(return_value=0))
    monkeypatch.setattr(invoice_generator.invoice_repo, "create_invoice", create_invoice)
    monkeypatch.setattr(invoice_generator.invoice_lines_repo, "create_invoice_line", create_invoice_line)
    monkeypatch.setattr(invoice_generator.recurring_items_repo, "mark_recurring_invoice_items_billed", mark_recurring)
    monkeypatch.setattr(invoice_generator.billed_time_repo, "create_billed_time_entry", create_billed_time_entry)
    monkeypatch.setattr(invoice_generator.tickets_repo, "update_ticket", update_ticket)
    monkeypatch.setattr(invoice_generator.xero_service, "resolve_invoiced_ticket_status", lambda: "closed")

    result = asyncio.run(
        invoice_generator.generate_invoice(
            1,
            ticket_ids=[2],
            include_recurring_items=False,
            approval_required=True,
        )
    )

    assert result["status"] == "succeeded"
    assert result["tickets_billed"] == 1
    assert recurring_builder.await_count == 0
    assert create_invoice.await_args.kwargs["approval_required"] is True
    assert create_invoice.await_args.kwargs["status"] == "pending_approval"
    update_ticket.assert_awaited_once()
    assert update_ticket.await_args.args[0] == 2
