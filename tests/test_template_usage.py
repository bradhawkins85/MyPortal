"""Test to verify the template is used exactly as configured."""
import pytest
from decimal import Decimal
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
import json

from app.services import xero as xero_service


@pytest.mark.anyio("asyncio")
async def test_sync_company_uses_exact_template():
    """Verify that sync_company uses the exact template format from settings."""
    
    # Use a very specific template to make sure it's being used
    custom_template = "CUSTOM: Ticket #{ticket_id} | Subject: {ticket_subject} | Labour: {labour_name}"
    
    module_settings = {
        "enabled": True,
        "settings": {
            "client_id": "test-id",
            "client_secret": "test-secret",
            "refresh_token": "test-token",
            "tenant_id": "test-tenant",
            "account_code": "400",
            "tax_type": "OUTPUT",
            "line_amount_type": "Exclusive",
            "reference_prefix": "TEST",
            "default_hourly_rate": "100.00",
            "billable_statuses": "billable",
            "line_item_description_template": custom_template,
        },
    }
    
    company = {
        "id": 1,
        "name": "Test Company",
        "xero_id": "xero-123",
    }
    
    tickets = [
        {
            "id": 42,
            "status": "billable",
            "company_id": 1,
            "subject": "Test Issue",
        },
    ]
    
    replies = [
        {
            "id": 100,
            "minutes_spent": 60,
            "is_billable": True,
            "labour_type_id": 1,
            "labour_type_code": "SUPPORT",
            "labour_type_name": "Technical Support",
            "labour_type_rate": None,  # Use default rate
        },
    ]
    
    unbilled_reply_ids = {100}
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps({
        "Invoices": [{
            "InvoiceNumber": "INV-TEST",
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
        
        mock_modules.get_module = AsyncMock(return_value=module_settings)
        mock_modules.acquire_xero_access_token = AsyncMock(return_value="test-token")
        
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
        
        # Get the invoice payload that was sent
        call_args = mock_client_instance.post.call_args
        invoice_payload = call_args[1]["json"]["Invoices"][0]
        
        line_items = invoice_payload["LineItems"]
        assert len(line_items) == 1
        
        # Verify the description matches our custom template EXACTLY
        expected_description = "CUSTOM: Ticket #42 | Subject: Test Issue | Labour: Technical Support"
        actual_description = line_items[0]["Description"]
        
        print(f"Expected: {expected_description}")
        print(f"Actual:   {actual_description}")
        
        assert actual_description == expected_description, \
            f"Template not applied correctly. Expected '{expected_description}' but got '{actual_description}'"


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_sync_company_uses_exact_template())
