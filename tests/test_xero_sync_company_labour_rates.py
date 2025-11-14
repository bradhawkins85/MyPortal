"""Tests for sync_company function with labour type rates."""
import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import xero as xero_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_sync_company_creates_line_items_with_labour_rates():
    """Test that sync_company creates line items when labour types have rates configured.
    
    This test verifies the fix for the issue where line items were only created
    when local_rate was None due to incorrect indentation.
    """
    
    # Mock module settings
    module_settings = {
        "enabled": True,
        "settings": {
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "refresh_token": "test-refresh-token",
            "tenant_id": "test-tenant-id",
            "account_code": "400",
            "tax_type": "OUTPUT",
            "line_amount_type": "Exclusive",
            "reference_prefix": "Support",
            "default_hourly_rate": "100.00",
            "billable_statuses": "resolved,closed",
            "line_item_description_template": "Ticket {ticket_id}: {ticket_subject} - {labour_name}",
        },
    }
    
    # Mock company data
    company = {
        "id": 1,
        "name": "Test Company",
        "xero_id": "xero-test-123",
    }
    
    # Mock tickets with unbilled time entries
    tickets = [
        {
            "id": 1,
            "status": "resolved",
            "company_id": 1,
            "subject": "Network Issue",
        },
    ]
    
    # Mock replies with labour types that have rates
    replies = [
        {
            "id": 10,
            "minutes_spent": 60,
            "is_billable": True,
            "labour_type_id": 1,
            "labour_type_code": "REMOTE",
            "labour_type_name": "Remote Support",
            "labour_type_rate": Decimal("150.00"),  # This rate should be used
        },
        {
            "id": 11,
            "minutes_spent": 30,
            "is_billable": True,
            "labour_type_id": 2,
            "labour_type_code": "ONSITE",
            "labour_type_name": "On-site Support",
            "labour_type_rate": Decimal("200.00"),  # This rate should be used
        },
    ]
    
    unbilled_reply_ids = {10, 11}
    
    # Mock successful Xero API response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps({
        "Invoices": [{
            "InvoiceNumber": "INV-100",
            "Status": "DRAFT",
        }]
    })
    mock_response.headers = {}
    
    with patch("app.services.xero.modules_service") as mock_modules, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.recurring_items_repo") as mock_recurring_repo, \
         patch("app.services.xero.assets_repo") as mock_assets_repo, \
         patch("app.services.xero.webhook_monitor") as mock_webhook, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client:
        
        # Setup mocks
        mock_modules.get_module = AsyncMock(return_value=module_settings)
        mock_modules.acquire_xero_access_token = AsyncMock(return_value="test-access-token")
        
        mock_company_repo.get_company_by_id = AsyncMock(return_value=company)
        
        mock_tickets_repo.list_tickets = AsyncMock(return_value=tickets)
        mock_tickets_repo.list_replies = AsyncMock(return_value=replies)
        mock_tickets_repo.update_ticket = AsyncMock()
        
        mock_billed_repo.get_unbilled_reply_ids = AsyncMock(return_value=unbilled_reply_ids)
        mock_billed_repo.create_billed_time_entry = AsyncMock()
        
        mock_recurring_repo.list_company_recurring_invoice_items = AsyncMock(return_value=[])
        
        # Mock asset counts (for recurring items context)
        mock_assets_repo.count_active_assets = AsyncMock(return_value=0)
        mock_assets_repo.count_active_assets_by_type = AsyncMock(return_value=0)
        
        mock_webhook.create_manual_event = AsyncMock(return_value={"id": 1})
        mock_webhook.record_manual_success = AsyncMock()
        
        # Mock HTTP client
        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client.return_value.__aexit__ = AsyncMock()
        
        # Call sync_company
        result = await xero_service.sync_company(company_id=1)
        
        # Verify the sync succeeded
        assert result["status"] == "succeeded"
        assert result["invoice_number"] == "INV-100"
        assert result["tickets_billed"] == 1
        
        # Verify that HTTP POST was called to Xero
        assert mock_client_instance.post.called
        
        # Get the invoice payload that was sent
        call_args = mock_client_instance.post.call_args
        invoice_payload = call_args[1]["json"]["Invoices"][0]
        
        # Verify we have 2 line items (one for each labour type)
        line_items = invoice_payload["LineItems"]
        assert len(line_items) == 2, f"Expected 2 line items, got {len(line_items)}"
        
        # Sort line items by ItemCode for consistent checking
        line_items_sorted = sorted(line_items, key=lambda x: x.get("ItemCode", ""))
        
        # Verify first line item (ONSITE - 30 min = 0.5 hr at $200/hr)
        onsite_item = line_items_sorted[0]
        assert onsite_item["ItemCode"] == "ONSITE", f"Expected ItemCode 'ONSITE', got {onsite_item.get('ItemCode')}"
        assert onsite_item["Quantity"] == 0.5, f"Expected Quantity 0.5, got {onsite_item['Quantity']}"
        assert onsite_item["UnitAmount"] == 200.00, f"Expected UnitAmount 200.00, got {onsite_item['UnitAmount']}"
        assert "On-site Support" in onsite_item["Description"]
        
        # Verify second line item (REMOTE - 60 min = 1.0 hr at $150/hr)
        remote_item = line_items_sorted[1]
        assert remote_item["ItemCode"] == "REMOTE", f"Expected ItemCode 'REMOTE', got {remote_item.get('ItemCode')}"
        assert remote_item["Quantity"] == 1.0, f"Expected Quantity 1.0, got {remote_item['Quantity']}"
        assert remote_item["UnitAmount"] == 150.00, f"Expected UnitAmount 150.00, got {remote_item['UnitAmount']}"
        assert "Remote Support" in remote_item["Description"]


@pytest.mark.anyio("asyncio")
async def test_sync_company_uses_default_rate_when_no_labour_rate():
    """Test that sync_company falls back to default rate when labour type has no rate."""
    
    # Mock module settings
    module_settings = {
        "enabled": True,
        "settings": {
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "refresh_token": "test-refresh-token",
            "tenant_id": "test-tenant-id",
            "account_code": "400",
            "tax_type": "OUTPUT",
            "line_amount_type": "Exclusive",
            "reference_prefix": "Support",
            "default_hourly_rate": "100.00",
            "billable_statuses": "resolved",
            "line_item_description_template": "Ticket {ticket_id}",
        },
    }
    
    company = {
        "id": 1,
        "name": "Test Company",
        "xero_id": "xero-test-123",
    }
    
    tickets = [
        {
            "id": 1,
            "status": "resolved",
            "company_id": 1,
            "subject": "Test Ticket",
        },
    ]
    
    # Reply with labour type but NO rate (should use default)
    replies = [
        {
            "id": 10,
            "minutes_spent": 60,
            "is_billable": True,
            "labour_type_id": 1,
            "labour_type_code": "SUPPORT",
            "labour_type_name": "General Support",
            "labour_type_rate": None,  # No rate configured
        },
    ]
    
    unbilled_reply_ids = {10}
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps({"Invoices": [{"InvoiceNumber": "INV-200"}]})
    mock_response.headers = {}
    
    with patch("app.services.xero.modules_service") as mock_modules, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.recurring_items_repo") as mock_recurring_repo, \
         patch("app.services.xero.assets_repo") as mock_assets_repo, \
         patch("app.services.xero.webhook_monitor") as mock_webhook, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client:
        
        mock_modules.get_module = AsyncMock(return_value=module_settings)
        mock_modules.acquire_xero_access_token = AsyncMock(return_value="test-access-token")
        
        mock_company_repo.get_company_by_id = AsyncMock(return_value=company)
        
        mock_tickets_repo.list_tickets = AsyncMock(return_value=tickets)
        mock_tickets_repo.list_replies = AsyncMock(return_value=replies)
        mock_tickets_repo.update_ticket = AsyncMock()
        
        mock_billed_repo.get_unbilled_reply_ids = AsyncMock(return_value=unbilled_reply_ids)
        mock_billed_repo.create_billed_time_entry = AsyncMock()
        
        mock_recurring_repo.list_company_recurring_invoice_items = AsyncMock(return_value=[])
        
        mock_assets_repo.count_active_assets = AsyncMock(return_value=0)
        mock_assets_repo.count_active_assets_by_type = AsyncMock(return_value=0)
        
        mock_webhook.create_manual_event = AsyncMock(return_value={"id": 1})
        mock_webhook.record_manual_success = AsyncMock()
        
        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client.return_value.__aexit__ = AsyncMock()
        
        result = await xero_service.sync_company(company_id=1)
        
        assert result["status"] == "succeeded"
        
        # Get the invoice payload
        call_args = mock_client_instance.post.call_args
        invoice_payload = call_args[1]["json"]["Invoices"][0]
        
        # Verify line item uses default rate
        line_items = invoice_payload["LineItems"]
        assert len(line_items) == 1
        
        line_item = line_items[0]
        assert line_item["ItemCode"] == "SUPPORT"
        assert line_item["Quantity"] == 1.0
        assert line_item["UnitAmount"] == 100.00, f"Expected default rate 100.00, got {line_item['UnitAmount']}"
