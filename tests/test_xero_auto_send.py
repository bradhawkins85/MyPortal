"""Tests for auto-send functionality in Xero sync."""
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import json

import pytest

from app.services import xero as xero_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_sync_billable_tickets_auto_send_sets_authorised_status():
    """Test that auto_send=True sets invoice status to AUTHORISED and SentToContact=True."""
    
    tickets = [
        {"id": 1, "status": "resolved", "company_id": 1, "subject": "Test Ticket"},
    ]
    
    unbilled_reply_ids = {10}
    
    # Mock HTTP response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps({
        "Invoices": [{"InvoiceNumber": "INV-001"}]
    })
    mock_response.headers = {}
    
    with patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.webhook_monitor") as mock_webhook, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client:
        
        mock_tickets_repo.list_tickets = AsyncMock(return_value=tickets)
        mock_billed_repo.get_unbilled_reply_ids = AsyncMock(return_value=unbilled_reply_ids)
        mock_company_repo.get_company_by_id = AsyncMock(return_value={
            "id": 1,
            "name": "Test Company",
            "xero_id": "xero-123",
        })
        mock_tickets_repo.get_ticket = AsyncMock(side_effect=lambda tid: next(t for t in tickets if t["id"] == tid))
        mock_tickets_repo.list_replies = AsyncMock(return_value=[
            {"id": 10, "minutes_spent": 60, "is_billable": True, "labour_type_id": None},
        ])
        mock_tickets_repo.update_ticket = AsyncMock()
        mock_billed_repo.create_billed_time_entry = AsyncMock()
        
        mock_webhook.create_manual_event = AsyncMock(return_value={"id": 1})
        mock_webhook.record_manual_success = AsyncMock()
        
        # Mock HTTP client
        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client.return_value.__aexit__ = AsyncMock()
        
        # Call with auto_send=True
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
            auto_send=True,
        )
        
        # Verify the invoice payload has AUTHORISED status and SentToContact
        assert mock_client_instance.post.called
        call_args = mock_client_instance.post.call_args
        invoice_payload = call_args.kwargs.get("json")
        
        assert invoice_payload is not None
        assert invoice_payload["Status"] == "AUTHORISED"
        assert invoice_payload["SentToContact"] is True
        assert result["status"] == "succeeded"


@pytest.mark.anyio("asyncio")
async def test_sync_billable_tickets_draft_status_without_auto_send():
    """Test that auto_send=False keeps invoice status as DRAFT without SentToContact."""
    
    tickets = [
        {"id": 1, "status": "resolved", "company_id": 1, "subject": "Test Ticket"},
    ]
    
    unbilled_reply_ids = {10}
    
    # Mock HTTP response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps({
        "Invoices": [{"InvoiceNumber": "INV-001"}]
    })
    mock_response.headers = {}
    
    with patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.webhook_monitor") as mock_webhook, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client:
        
        mock_tickets_repo.list_tickets = AsyncMock(return_value=tickets)
        mock_billed_repo.get_unbilled_reply_ids = AsyncMock(return_value=unbilled_reply_ids)
        mock_company_repo.get_company_by_id = AsyncMock(return_value={
            "id": 1,
            "name": "Test Company",
            "xero_id": "xero-123",
        })
        mock_tickets_repo.get_ticket = AsyncMock(side_effect=lambda tid: next(t for t in tickets if t["id"] == tid))
        mock_tickets_repo.list_replies = AsyncMock(return_value=[
            {"id": 10, "minutes_spent": 60, "is_billable": True, "labour_type_id": None},
        ])
        mock_tickets_repo.update_ticket = AsyncMock()
        mock_billed_repo.create_billed_time_entry = AsyncMock()
        
        mock_webhook.create_manual_event = AsyncMock(return_value={"id": 1})
        mock_webhook.record_manual_success = AsyncMock()
        
        # Mock HTTP client
        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client.return_value.__aexit__ = AsyncMock()
        
        # Call with auto_send=False (default)
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
            auto_send=False,
        )
        
        # Verify the invoice payload has DRAFT status and no SentToContact
        assert mock_client_instance.post.called
        call_args = mock_client_instance.post.call_args
        invoice_payload = call_args.kwargs.get("json")
        
        assert invoice_payload is not None
        assert invoice_payload["Status"] == "DRAFT"
        assert "SentToContact" not in invoice_payload
        assert result["status"] == "succeeded"


@pytest.mark.anyio("asyncio")
async def test_sync_company_auto_send_parameter():
    """Test that sync_company accepts and passes auto_send parameter."""
    
    with patch("app.services.xero.modules_service") as mock_modules, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.recurring_items_repo") as mock_recurring_repo:
        
        # Module not configured, should skip early
        mock_modules.get_module = AsyncMock(return_value=None)
        
        result = await xero_service.sync_company(company_id=1, auto_send=True)
        
        assert result["status"] == "skipped"
        assert result["reason"] == "Module disabled"
