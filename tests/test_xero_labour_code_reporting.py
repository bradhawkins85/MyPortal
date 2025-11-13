"""Tests for labour code reporting in Xero sync results."""
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
async def test_sync_billable_tickets_includes_labour_codes_in_success():
    """Test that successful sync includes labour code information in result."""
    
    tickets = [
        {"id": 1, "status": "resolved", "company_id": 1, "subject": "Test ticket"},
    ]
    
    unbilled_reply_ids = {10, 11}
    
    # Mock Xero API response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps({"Invoices": [{"InvoiceNumber": "INV-100"}]})
    mock_response.headers = {}
    
    with patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.webhook_monitor") as mock_webhook, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client, \
         patch("app.services.xero.fetch_xero_item_rates") as mock_fetch_rates:
        
        mock_tickets_repo.list_tickets = AsyncMock(return_value=tickets)
        mock_tickets_repo.get_ticket = AsyncMock(return_value=tickets[0])
        mock_tickets_repo.list_replies = AsyncMock(return_value=[
            {
                "id": 10,
                "minutes_spent": 30,
                "is_billable": True,
                "labour_type_id": 1,
                "labour_type_code": "REMOTE",
                "labour_type_name": "Remote Support",
                "labour_type_rate": None,
            },
            {
                "id": 11,
                "minutes_spent": 60,
                "is_billable": True,
                "labour_type_id": 2,
                "labour_type_code": "ONSITE",
                "labour_type_name": "On-site Support",
                "labour_type_rate": None,
            },
        ])
        mock_tickets_repo.update_ticket = AsyncMock()
        
        mock_billed_repo.get_unbilled_reply_ids = AsyncMock(return_value=unbilled_reply_ids)
        mock_billed_repo.create_billed_time_entry = AsyncMock()
        
        mock_company_repo.get_company_by_id = AsyncMock(return_value={
            "id": 1,
            "name": "Test Company",
            "xero_id": "xero-123",
        })
        
        # Mock fetch_xero_item_rates to return rate for REMOTE but not ONSITE
        mock_fetch_rates.return_value = {
            "REMOTE": Decimal("95.00")
        }
        
        mock_webhook.create_manual_event = AsyncMock(return_value={"id": 1})
        mock_webhook.record_manual_success = AsyncMock()
        
        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_client_instance
        
        result = await xero_service.sync_billable_tickets(
            company_id=1,
            billable_statuses=["resolved"],
            hourly_rate=Decimal("100"),
            account_code="400",
            tax_type="OUTPUT",
            line_amount_type="Exclusive",
            reference_prefix="Support",
            description_template="Ticket {ticket_id}: {ticket_subject}",
            tenant_id="tenant-123",
            access_token="access-token-123",
        )
        
        # Verify result includes labour code information
        assert result["status"] == "succeeded"
        assert "labour_codes_expected" in result
        assert set(result["labour_codes_expected"]) == {"REMOTE", "ONSITE"}
        assert "labour_codes_found_in_xero" in result
        assert result["labour_codes_found_in_xero"] == ["REMOTE"]
        assert "labour_codes_missing_from_xero" in result
        assert result["labour_codes_missing_from_xero"] == ["ONSITE"]


@pytest.mark.anyio("asyncio")
async def test_sync_billable_tickets_includes_labour_codes_in_failure():
    """Test that failed sync includes labour code information in result."""
    
    tickets = [
        {"id": 1, "status": "resolved", "company_id": 1, "subject": "Test ticket"},
    ]
    
    unbilled_reply_ids = {10}
    
    # Mock Xero API response with error
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request"
    mock_response.headers = {}
    
    with patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.webhook_monitor") as mock_webhook, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client, \
         patch("app.services.xero.fetch_xero_item_rates") as mock_fetch_rates:
        
        mock_tickets_repo.list_tickets = AsyncMock(return_value=tickets)
        mock_tickets_repo.get_ticket = AsyncMock(return_value=tickets[0])
        mock_tickets_repo.list_replies = AsyncMock(return_value=[
            {
                "id": 10,
                "minutes_spent": 30,
                "is_billable": True,
                "labour_type_id": 1,
                "labour_type_code": "SUPPORT",
                "labour_type_name": "Support",
                "labour_type_rate": None,
            },
        ])
        
        mock_billed_repo.get_unbilled_reply_ids = AsyncMock(return_value=unbilled_reply_ids)
        
        mock_company_repo.get_company_by_id = AsyncMock(return_value={
            "id": 1,
            "name": "Test Company",
            "xero_id": "xero-123",
        })
        
        # Mock fetch_xero_item_rates to return empty (no rates found)
        mock_fetch_rates.return_value = {}
        
        mock_webhook.create_manual_event = AsyncMock(return_value={"id": 1})
        mock_webhook.record_manual_failure = AsyncMock()
        
        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_client_instance
        
        result = await xero_service.sync_billable_tickets(
            company_id=1,
            billable_statuses=["resolved"],
            hourly_rate=Decimal("100"),
            account_code="400",
            tax_type="OUTPUT",
            line_amount_type="Exclusive",
            reference_prefix="Support",
            description_template="Ticket {ticket_id}: {ticket_subject}",
            tenant_id="tenant-123",
            access_token="access-token-123",
        )
        
        # Verify result includes labour code information even on failure
        assert result["status"] == "failed"
        assert "labour_codes_expected" in result
        assert result["labour_codes_expected"] == ["SUPPORT"]
        assert "labour_codes_found_in_xero" in result
        assert result["labour_codes_found_in_xero"] == []
        assert "labour_codes_missing_from_xero" in result
        assert result["labour_codes_missing_from_xero"] == ["SUPPORT"]


@pytest.mark.anyio("asyncio")
async def test_sync_company_includes_labour_codes_in_success():
    """Test that successful company sync includes labour code information."""
    
    tickets = [
        {"id": 1, "status": "resolved", "company_id": 1, "subject": "Test ticket"},
    ]
    
    unbilled_reply_ids = {10}
    
    # Mock Xero API response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps({"Invoices": [{"InvoiceNumber": "INV-200"}]})
    mock_response.headers = {}
    
    with patch("app.services.xero.modules_service") as mock_modules, \
         patch("app.services.xero.company_repo") as mock_company_repo, \
         patch("app.services.xero.recurring_items_repo") as mock_recurring_repo, \
         patch("app.services.xero.tickets_repo") as mock_tickets_repo, \
         patch("app.services.xero.billed_time_repo") as mock_billed_repo, \
         patch("app.services.xero.assets_repo") as mock_assets_repo, \
         patch("app.services.xero.webhook_monitor") as mock_webhook, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client, \
         patch("app.services.xero.fetch_xero_item_rates") as mock_fetch_rates:
        
        mock_modules.get_module = AsyncMock(return_value={
            "enabled": True,
            "settings": {
                "client_id": "test",
                "client_secret": "test",
                "refresh_token": "test",
                "tenant_id": "tenant-123",
                "billable_statuses": "resolved",
                "default_hourly_rate": "100",
                "account_code": "400",
                "tax_type": "OUTPUT",
                "line_amount_type": "Exclusive",
                "reference_prefix": "Support",
            }
        })
        mock_modules.acquire_xero_access_token = AsyncMock(return_value="access-token-123")
        
        mock_company_repo.get_company_by_id = AsyncMock(return_value={
            "id": 1,
            "name": "Test Company",
            "xero_id": "xero-123",
        })
        
        mock_recurring_repo.list_company_recurring_invoice_items = AsyncMock(return_value=[])
        
        mock_assets_repo.count_active_assets = AsyncMock(return_value=5)
        mock_assets_repo.count_active_assets_by_type = AsyncMock(return_value=0)
        
        mock_tickets_repo.list_tickets = AsyncMock(return_value=tickets)
        mock_tickets_repo.list_replies = AsyncMock(return_value=[
            {
                "id": 10,
                "minutes_spent": 45,
                "is_billable": True,
                "labour_type_id": 1,
                "labour_type_code": "CONSULT",
                "labour_type_name": "Consulting",
                "labour_type_rate": None,
            },
        ])
        mock_tickets_repo.update_ticket = AsyncMock()
        
        mock_billed_repo.get_unbilled_reply_ids = AsyncMock(return_value=unbilled_reply_ids)
        mock_billed_repo.create_billed_time_entry = AsyncMock()
        
        # Mock fetch_xero_item_rates to return a rate
        mock_fetch_rates.return_value = {
            "CONSULT": Decimal("150.00")
        }
        
        mock_webhook.create_manual_event = AsyncMock(return_value={"id": 1})
        mock_webhook.record_manual_success = AsyncMock()
        
        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_client_instance
        
        result = await xero_service.sync_company(company_id=1)
        
        # Verify result includes labour code information
        assert result["status"] == "succeeded"
        assert "labour_codes_expected" in result
        assert result["labour_codes_expected"] == ["CONSULT"]
        assert "labour_codes_found_in_xero" in result
        assert result["labour_codes_found_in_xero"] == ["CONSULT"]
        # No missing codes in this case
        assert "labour_codes_missing_from_xero" not in result
