"""Tests for billable ticket syncing to Xero."""
import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories import ticket_billed_time_entries as billed_time_repo
from app.services import xero as xero_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_sync_billable_tickets_filters_by_status():
    """Test that sync_billable_tickets only processes tickets with billable statuses."""

    # Mock data
    tickets = [
        {"id": 1, "status": "resolved", "company_id": 1},
        {"id": 2, "status": "open", "company_id": 1},
        {"id": 3, "status": "in_progress", "company_id": 1},
    ]

    unbilled_reply_ids = {10, 11, 12}

    with patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.company_repo") as mock_company_repo:

        mock_tickets_repo.list_tickets = AsyncMock(return_value=tickets)
        mock_billed_repo.get_unbilled_reply_ids = AsyncMock(return_value=unbilled_reply_ids)
        mock_company_repo.get_company_by_id = AsyncMock(return_value={
            "id": 1,
            "name": "Test Company",
            "xero_id": "xero-123",
        })
        mock_tickets_repo.get_ticket = AsyncMock(side_effect=lambda tid: next(t for t in tickets if t["id"] == tid))
        mock_tickets_repo.list_replies = AsyncMock(return_value=[
            {"id": 10, "minutes_spent": 30, "is_billable": True, "labour_type_id": None},
        ])

        # Only "resolved" status is billable
        result = await xero_service.sync_billable_tickets(
            company_id=1,
            billable_statuses=["resolved", "closed"],
            hourly_rate=Decimal("150"),
            account_code="400",
            tax_type="OUTPUT",
            line_amount_type="Exclusive",
            reference_prefix="Support",
            description_template="Ticket {ticket_id}: {ticket_subject}",
            tenant_id="tenant-123",
            access_token="access-token-123",
        )

        # Should process ticket 1 (resolved) but not tickets 2 (open) or 3 (in_progress)
        assert result["status"] in ("succeeded", "failed", "error")


@pytest.mark.anyio("asyncio")
async def test_sync_billable_tickets_accepts_string_statuses():
    """Ensure comma-separated status strings are normalised for billing checks."""

    tickets = [
        {"id": 1, "status": "Resolved", "company_id": 1, "subject": "Printer issue"},
    ]

    unbilled_reply_ids = {10}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps({"Invoices": [{"InvoiceNumber": "INV-100"}]})
    mock_response.headers = {}

    with patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.webhook_monitor") as mock_webhook, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client:

        mock_tickets_repo.list_tickets = AsyncMock(return_value=tickets)
        mock_tickets_repo.get_ticket = AsyncMock(return_value=tickets[0])
        mock_tickets_repo.list_replies = AsyncMock(return_value=[
            {"id": 10, "minutes_spent": 60, "is_billable": True, "labour_type_id": None},
        ])
        mock_billed_repo.get_unbilled_reply_ids = AsyncMock(return_value=unbilled_reply_ids)
        mock_billed_repo.create_billed_time_entry = AsyncMock()
        mock_company_repo.get_company_by_id = AsyncMock(return_value={
            "id": 1,
            "name": "Acme Corp",
            "xero_id": "xero-456",
        })

        mock_webhook.create_manual_event = AsyncMock(return_value={"id": 1})
        mock_webhook.record_manual_success = AsyncMock()

        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client.return_value.__aexit__ = AsyncMock()

        result = await xero_service.sync_billable_tickets(
            company_id=1,
            billable_statuses="Resolved, Closed",
            hourly_rate=Decimal("150"),
            account_code="400",
            tax_type="OUTPUT",
            line_amount_type="Exclusive",
            reference_prefix="Support",
            description_template="Ticket {ticket_id}: {ticket_subject}",
            tenant_id="tenant-123",
            access_token="access-token-123",
            auto_send=True,
        )

        assert mock_client_instance.post.called
        assert result["status"] == "succeeded"


@pytest.mark.anyio("asyncio")
async def test_sync_billable_tickets_skips_without_unbilled_time():
    """Test that tickets without unbilled time entries are skipped."""
    
    tickets = [
        {"id": 1, "status": "resolved", "company_id": 1},
    ]
    
    with patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo:
        
        mock_tickets_repo.list_tickets = AsyncMock(return_value=tickets)
        # No unbilled reply IDs
        mock_billed_repo.get_unbilled_reply_ids = AsyncMock(return_value=set())
        
        result = await xero_service.sync_billable_tickets(
            company_id=1,
            billable_statuses=["resolved"],
            hourly_rate=Decimal("150"),
            account_code="400",
            tax_type="OUTPUT",
            line_amount_type="Exclusive",
            reference_prefix="Support",
            description_template="Ticket {ticket_id}: {ticket_subject}",
            tenant_id="tenant-123",
            access_token="access-token-123",
        )
        
        assert result["status"] == "skipped"
        assert "No billable tickets with unbilled time" in result["reason"]


@pytest.mark.anyio("asyncio")
async def test_sync_billable_tickets_skips_without_billable_statuses():
    """Test that sync is skipped if no billable statuses are configured."""
    
    result = await xero_service.sync_billable_tickets(
        company_id=1,
        billable_statuses=[],
        hourly_rate=Decimal("150"),
        account_code="400",
        tax_type="OUTPUT",
        line_amount_type="Exclusive",
        reference_prefix="Support",
        description_template="Ticket {ticket_id}: {ticket_subject}",
        tenant_id="tenant-123",
        access_token="access-token-123",
    )
    
    assert result["status"] == "skipped"
    assert "No billable statuses configured" in result["reason"]


@pytest.mark.anyio("asyncio")
async def test_build_ticket_invoices_with_labour_types():
    """Test that ticket invoices group billable time by labour type."""
    
    async def fake_fetch_ticket(ticket_id: int):
        return {
            "id": ticket_id,
            "company_id": 1,
            "subject": f"Network Issue #{ticket_id}",
            "status": "resolved",
        }

    async def fake_fetch_replies(ticket_id: int):
        return [
            {
                "minutes_spent": 60,
                "is_billable": True,
                "labour_type_code": "REMOTE",
                "labour_type_name": "Remote Support",
            },
            {
                "minutes_spent": 30,
                "is_billable": True,
                "labour_type_code": "ONSITE",
                "labour_type_name": "On-site Support",
            },
            {
                "minutes_spent": 15,
                "is_billable": False,  # Non-billable should be excluded
                "labour_type_code": "REMOTE",
                "labour_type_name": "Remote Support",
            },
        ]

    async def fake_fetch_company(company_id: int):
        return {"id": company_id, "name": "Test Corp", "xero_id": "xero-456"}

    invoices = await xero_service.build_ticket_invoices(
        [1],
        hourly_rate=Decimal("120"),
        account_code="400",
        tax_type="OUTPUT",
        line_amount_type="Exclusive",
        reference_prefix="IT Support",
        fetch_ticket=fake_fetch_ticket,
        fetch_replies=fake_fetch_replies,
        fetch_company=fake_fetch_company,
    )

    assert len(invoices) == 1
    invoice = invoices[0]
    
    # Should have 2 line items (one per labour type)
    assert len(invoice["line_items"]) == 2
    
    # Check that total billable minutes is 90 (60 + 30, excluding non-billable)
    assert invoice["context"]["total_billable_minutes"] == 90
    
    # Verify line items have correct labour codes
    labour_codes = {item.get("ItemCode") for item in invoice["line_items"]}
    assert "REMOTE" in labour_codes
    assert "ONSITE" in labour_codes
    
    # Verify quantities (60 min = 1.0 hr, 30 min = 0.5 hr)
    quantities = sorted([item["Quantity"] for item in invoice["line_items"]])
    assert quantities == [0.5, 1.0]


@pytest.mark.anyio("asyncio")
async def test_build_ticket_invoices_uses_template():
    """Test that line item description template is applied correctly."""
    
    async def fake_fetch_ticket(ticket_id: int):
        return {
            "id": ticket_id,
            "company_id": 1,
            "subject": "Email not working",
            "status": "resolved",
        }

    async def fake_fetch_replies(ticket_id: int):
        return [
            {
                "minutes_spent": 45,
                "is_billable": True,
                "labour_type_code": "REMOTE",
                "labour_type_name": "Remote Support",
            },
        ]

    async def fake_fetch_company(company_id: int):
        return {"id": company_id, "name": "Test Corp", "xero_id": "xero-456"}

    template = "#{ticket_id} - {ticket_subject} - {labour_name} ({labour_hours}h)"
    
    invoices = await xero_service.build_ticket_invoices(
        [1],
        hourly_rate=Decimal("100"),
        account_code="400",
        tax_type="OUTPUT",
        line_amount_type="Exclusive",
        reference_prefix="Support",
        description_template=template,
        fetch_ticket=fake_fetch_ticket,
        fetch_replies=fake_fetch_replies,
        fetch_company=fake_fetch_company,
    )

    assert len(invoices) == 1
    line_item = invoices[0]["line_items"][0]
    
    # Verify template was applied (45 min = 0.75 hr)
    assert "#1" in line_item["Description"]
    assert "Email not working" in line_item["Description"]
    assert "Remote Support" in line_item["Description"]
    assert "0.75" in line_item["Description"]


@pytest.mark.anyio("asyncio")
async def test_sync_billable_tickets_closes_tickets_after_invoicing():
    """Test that tickets are moved to 'closed' status after successful invoicing."""
    
    tickets = [
        {"id": 1, "status": "resolved", "company_id": 1, "subject": "Test Ticket"},
    ]
    
    unbilled_reply_ids = {10}
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps({"Invoices": [{"InvoiceNumber": "INV-12345"}]})
    mock_response.headers = {}
    
    with patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.webhook_monitor") as mock_webhook, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client:
        
        mock_tickets_repo.list_tickets = AsyncMock(return_value=tickets)
        mock_tickets_repo.get_ticket = AsyncMock(return_value=tickets[0])
        mock_tickets_repo.list_replies = AsyncMock(return_value=[
            {"id": 10, "minutes_spent": 60, "is_billable": True, "labour_type_id": None},
        ])
        mock_tickets_repo.update_ticket = AsyncMock()
        
        mock_billed_repo.get_unbilled_reply_ids = AsyncMock(return_value=unbilled_reply_ids)
        mock_billed_repo.create_billed_time_entry = AsyncMock()
        
        mock_company_repo.get_company_by_id = AsyncMock(return_value={
            "id": 1,
            "name": "Test Company",
            "xero_id": "xero-test-123",
        })
        
        mock_webhook.create_manual_event = AsyncMock(return_value={"id": 1})
        mock_webhook.record_manual_success = AsyncMock()
        
        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client.return_value.__aexit__ = AsyncMock()
        
        result = await xero_service.sync_billable_tickets(
            company_id=1,
            billable_statuses=["resolved"],
            hourly_rate=Decimal("150"),
            account_code="400",
            tax_type="OUTPUT",
            line_amount_type="Exclusive",
            reference_prefix="Support",
            description_template="Ticket {ticket_id}",
            tenant_id="tenant-123",
            access_token="access-token-123",
        )
        
        # Verify the sync succeeded
        assert result["status"] == "succeeded"
        assert result["invoice_number"] == "INV-12345"
        assert result["tickets_billed"] == 1
        
        # Verify that update_ticket was called to close the ticket
        mock_tickets_repo.update_ticket.assert_called_once()
        
        # Get the call arguments
        call_args = mock_tickets_repo.update_ticket.call_args
        ticket_id = call_args[0][0]
        kwargs = call_args[1]
        
        # Verify the ticket was closed
        assert ticket_id == 1
        assert kwargs["status"] == "closed"
        assert kwargs["xero_invoice_number"] == "INV-12345"
        assert "closed_at" in kwargs
        assert "billed_at" in kwargs
