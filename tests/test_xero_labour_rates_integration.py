"""Integration test for Xero labour type rates functionality."""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.services import xero as xero_service


@pytest.mark.asyncio
async def test_fetch_xero_item_rates_with_valid_items():
    """Test that fetch_xero_item_rates correctly fetches rates from Xero API."""
    
    # Mock httpx.AsyncClient
    mock_response_remote = AsyncMock()
    mock_response_remote.status_code = 200
    mock_response_remote.json.return_value = {
        "Items": [
            {
                "Code": "REMOTE",
                "SalesDetails": {
                    "UnitPrice": "95.00"
                }
            }
        ]
    }
    
    mock_response_onsite = AsyncMock()
    mock_response_onsite.status_code = 200
    mock_response_onsite.json.return_value = {
        "Items": [
            {
                "Code": "ONSITE",
                "SalesDetails": {
                    "UnitPrice": "150.00"
                }
            }
        ]
    }
    
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    
    # Return different responses for different codes
    async def mock_get(url, headers=None, params=None):
        code_filter = params.get("where", "")
        if "REMOTE" in code_filter:
            return mock_response_remote
        elif "ONSITE" in code_filter:
            return mock_response_onsite
        else:
            response = AsyncMock()
            response.status_code = 404
            return response
    
    mock_client.get = mock_get
    
    with patch("httpx.AsyncClient", return_value=mock_client):
        rates = await xero_service.fetch_xero_item_rates(
            ["REMOTE", "ONSITE"],
            tenant_id="test-tenant",
            access_token="test-token",
        )
    
    # Verify rates were fetched correctly
    assert "REMOTE" in rates
    assert "ONSITE" in rates
    assert rates["REMOTE"] == Decimal("95.00")
    assert rates["ONSITE"] == Decimal("150.00")


@pytest.mark.asyncio
async def test_fetch_xero_item_rates_item_not_found():
    """Test that fetch_xero_item_rates handles items that don't exist in Xero."""
    
    mock_response = AsyncMock()
    mock_response.status_code = 404
    
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.return_value = mock_response
    
    with patch("httpx.AsyncClient", return_value=mock_client):
        rates = await xero_service.fetch_xero_item_rates(
            ["NONEXISTENT"],
            tenant_id="test-tenant",
            access_token="test-token",
        )
    
    # Should return empty dict for items not found
    assert rates == {}


@pytest.mark.asyncio
async def test_fetch_xero_item_rates_no_sales_price():
    """Test that fetch_xero_item_rates handles items without a sales price."""
    
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "Items": [
            {
                "Code": "NOPRICE",
                "SalesDetails": {}  # No UnitPrice
            }
        ]
    }
    
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.return_value = mock_response
    
    with patch("httpx.AsyncClient", return_value=mock_client):
        rates = await xero_service.fetch_xero_item_rates(
            ["NOPRICE"],
            tenant_id="test-tenant",
            access_token="test-token",
        )
    
    # Should return empty dict for items without price
    assert rates == {}


@pytest.mark.asyncio
async def test_sync_company_uses_xero_item_rates(monkeypatch):
    """Test that sync_company fetches and uses Xero item rates for labour types."""
    
    # Track whether fetch_xero_item_rates was called
    fetch_called = {"called": False, "item_codes": []}
    
    async def mock_fetch_xero_item_rates(item_codes, *, tenant_id, access_token):
        fetch_called["called"] = True
        fetch_called["item_codes"] = list(item_codes)
        # Return different rates for different labour codes
        return {
            "REMOTE": Decimal("95.00"),
            "ONSITE": Decimal("150.00"),
        }
    
    async def fake_get_module(slug: str, *, redact: bool = True):
        return {
            "enabled": True,
            "slug": "xero",
            "settings": {
                "client_id": "test_client",
                "client_secret": "test_secret",
                "refresh_token": "test_token",
                "tenant_id": "test_tenant",
                "billable_statuses": '["to bill"]',
                "default_hourly_rate": "100",  # Default rate that should be overridden
                "account_code": "400",
                "line_amount_type": "Exclusive",
                "reference_prefix": "IT Support",
            },
        }
    
    async def fake_acquire_token():
        return "test_access_token"
    
    async def fake_get_company(company_id: int):
        return {
            "id": company_id,
            "name": "Test Company",
            "xero_id": "xero-123",
        }
    
    async def fake_list_recurring_items(company_id: int):
        return []
    
    async def fake_list_tickets(company_id: int, **kwargs):
        return [
            {"id": 1, "status": "to bill", "company_id": company_id, "subject": "Test ticket"},
        ]
    
    async def fake_get_unbilled_reply_ids(ticket_id: int):
        return {100, 101}
    
    async def fake_list_replies(ticket_id: int, **kwargs):
        return [
            {
                "id": 100,
                "minutes_spent": 60,
                "is_billable": True,
                "labour_type_code": "REMOTE",
                "labour_type_name": "Remote Support",
                "labour_type_id": 1,
            },
            {
                "id": 101,
                "minutes_spent": 120,
                "is_billable": True,
                "labour_type_code": "ONSITE",
                "labour_type_name": "On-site Support",
                "labour_type_id": 2,
            },
        ]
    
    # Track the invoice payload sent to Xero
    invoice_payload = {"captured": None}
    
    async def fake_httpx_post(url, json=None, headers=None):
        invoice_payload["captured"] = json
        response = AsyncMock()
        response.status_code = 200
        response.text = '{"Invoices": [{"InvoiceNumber": "INV-12345"}]}'
        response.headers = {}
        response.json.return_value = {"Invoices": [{"InvoiceNumber": "INV-12345"}]}
        return response
    
    async def fake_webhook_create(*args, **kwargs):
        return {"id": 1}
    
    async def fake_webhook_record(*args, **kwargs):
        pass
    
    async def fake_create_billed_entry(*args, **kwargs):
        pass
    
    async def fake_update_ticket(*args, **kwargs):
        pass
    
    async def fake_count_assets(*args, **kwargs):
        return 0
    
    from app.services import modules as modules_service
    from app.repositories import companies as company_repo
    from app.repositories import company_recurring_invoice_items as recurring_items_repo
    from app.repositories import tickets as tickets_repo
    from app.repositories import ticket_billed_time_entries as billed_time_repo
    from app.repositories import assets as assets_repo
    from app.services import webhook_monitor
    import httpx
    
    # Set up mocks
    monkeypatch.setattr(modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service, "acquire_xero_access_token", fake_acquire_token)
    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(recurring_items_repo, "list_company_recurring_invoice_items", fake_list_recurring_items)
    monkeypatch.setattr(tickets_repo, "list_tickets", fake_list_tickets)
    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)
    monkeypatch.setattr(billed_time_repo, "get_unbilled_reply_ids", fake_get_unbilled_reply_ids)
    monkeypatch.setattr(billed_time_repo, "create_billed_time_entry", fake_create_billed_entry)
    monkeypatch.setattr(assets_repo, "count_active_assets", fake_count_assets)
    monkeypatch.setattr(assets_repo, "count_active_assets_by_type", fake_count_assets)
    monkeypatch.setattr(webhook_monitor, "create_manual_event", fake_webhook_create)
    monkeypatch.setattr(webhook_monitor, "record_manual_success", fake_webhook_record)
    
    # Mock fetch_xero_item_rates to track if it's called
    monkeypatch.setattr(xero_service, "fetch_xero_item_rates", mock_fetch_xero_item_rates)
    
    # Mock httpx.AsyncClient
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = fake_httpx_post
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: mock_client)
    
    # Call sync_company
    result = await xero_service.sync_company(company_id=1, auto_send=False)
    
    # Verify fetch_xero_item_rates was called
    assert fetch_called["called"], "fetch_xero_item_rates should have been called"
    assert "REMOTE" in fetch_called["item_codes"], "REMOTE labour code should be fetched"
    assert "ONSITE" in fetch_called["item_codes"], "ONSITE labour code should be fetched"
    
    # Verify the invoice was created successfully
    assert result["status"] == "succeeded"
    
    # Verify the invoice payload uses Xero item rates
    assert invoice_payload["captured"] is not None
    invoices = invoice_payload["captured"].get("Invoices", [])
    assert len(invoices) == 1
    
    line_items = invoices[0].get("LineItems", [])
    assert len(line_items) == 2
    
    # Find line items by ItemCode
    remote_item = next((item for item in line_items if item.get("ItemCode") == "REMOTE"), None)
    onsite_item = next((item for item in line_items if item.get("ItemCode") == "ONSITE"), None)
    
    assert remote_item is not None, "REMOTE line item should exist"
    assert onsite_item is not None, "ONSITE line item should exist"
    
    # Verify REMOTE uses Xero item rate: 60 minutes = 1 hour @ $95
    assert remote_item["Quantity"] == 1.0
    assert remote_item["UnitAmount"] == 95.00, f"Expected 95.00 but got {remote_item['UnitAmount']}"
    
    # Verify ONSITE uses Xero item rate: 120 minutes = 2 hours @ $150
    assert onsite_item["Quantity"] == 2.0
    assert onsite_item["UnitAmount"] == 150.00, f"Expected 150.00 but got {onsite_item['UnitAmount']}"
