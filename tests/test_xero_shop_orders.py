"""Tests for sending shop orders to Xero."""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import xero as xero_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_build_order_invoice_with_user_name():
    """Test that build_order_invoice adds user information line item."""
    
    async def fake_fetch_summary(order_number: str, company_id: int):
        return {
            "order_number": order_number,
            "status": "placed",
            "po_number": "PO-12345",
        }

    async def fake_fetch_items(order_number: str, company_id: int):
        return [
            {
                "quantity": 2,
                "price": Decimal("19.99"),
                "product_name": "Widget A",
                "sku": "WID-A",
            },
            {
                "quantity": 1,
                "price": Decimal("49.99"),
                "product_name": "Widget B",
                "sku": "WID-B",
            },
        ]

    async def fake_fetch_company(company_id: int):
        return {"id": company_id, "name": "Test Company", "xero_id": "xyz-789"}

    invoice = await xero_service.build_order_invoice(
        "ORD123456789012",
        1,
        account_code="200",
        tax_type="OUTPUT",
        line_amount_type="Exclusive",
        fetch_summary=fake_fetch_summary,
        fetch_items=fake_fetch_items,
        fetch_company=fake_fetch_company,
        user_name="John Doe",
    )

    assert invoice is not None
    assert len(invoice["line_items"]) == 3  # 2 products + 1 user info line
    
    # Check product line items
    assert invoice["line_items"][0]["Description"] == "Widget A"
    assert invoice["line_items"][0]["Quantity"] == 2
    assert invoice["line_items"][0]["UnitAmount"] == 19.99
    
    assert invoice["line_items"][1]["Description"] == "Widget B"
    assert invoice["line_items"][1]["Quantity"] == 1
    assert invoice["line_items"][1]["UnitAmount"] == 49.99
    
    # Check user info line item
    assert "John Doe" in invoice["line_items"][2]["Description"]
    assert "ORD123456789012" in invoice["line_items"][2]["Description"]
    assert invoice["line_items"][2]["Quantity"] == 0
    assert invoice["line_items"][2]["UnitAmount"] == 0
    
    # Check reference uses PO number
    assert invoice["reference"] == "PO-12345"
    
    # Check context includes user name
    assert invoice["context"]["user_name"] == "John Doe"


@pytest.mark.anyio("asyncio")
async def test_build_order_invoice_without_user_name():
    """Test that build_order_invoice works without user name."""
    
    async def fake_fetch_summary(order_number: str, company_id: int):
        return {
            "order_number": order_number,
            "status": "placed",
            "po_number": None,
        }

    async def fake_fetch_items(order_number: str, company_id: int):
        return [
            {
                "quantity": 1,
                "price": Decimal("99.99"),
                "product_name": "Product X",
                "sku": "PROD-X",
            },
        ]

    async def fake_fetch_company(company_id: int):
        return {"id": company_id, "name": "Acme Corp", "xero_id": "abc-123"}

    invoice = await xero_service.build_order_invoice(
        "ORD999888777666",
        1,
        account_code="200",
        tax_type=None,
        line_amount_type="Exclusive",
        fetch_summary=fake_fetch_summary,
        fetch_items=fake_fetch_items,
        fetch_company=fake_fetch_company,
        user_name=None,
    )

    assert invoice is not None
    assert len(invoice["line_items"]) == 1  # Only 1 product, no user info line
    assert invoice["line_items"][0]["Description"] == "Product X"
    
    # Check reference uses order number when no PO number
    assert invoice["reference"] == "ORD999888777666"
    
    # Check context includes None for user name
    assert invoice["context"]["user_name"] is None


@pytest.mark.anyio("asyncio")
async def test_send_order_to_xero_success():
    """Test successful order sending to Xero."""
    
    with patch("app.services.xero.modules_service.get_module") as mock_get_module, \
         patch("app.services.xero.modules_service.acquire_xero_access_token") as mock_get_token, \
         patch("app.services.xero.build_order_invoice") as mock_build_invoice, \
         patch("app.services.xero.company_repo.get_company_by_id") as mock_get_company, \
         patch("app.services.xero.webhook_monitor.create_manual_event") as mock_create_event, \
         patch("app.services.xero.webhook_monitor.record_manual_success") as mock_record_success, \
         patch("app.services.xero.httpx.AsyncClient") as mock_client_class:
        
        # Setup mocks
        mock_get_module.return_value = {
            "enabled": True,
            "settings": {
                "client_id": "test-client-id",
                "client_secret": "test-secret",
                "refresh_token": "test-refresh",
                "tenant_id": "test-tenant-id",
                "account_code": "200",
                "tax_type": "OUTPUT",
                "line_amount_type": "Exclusive",
            }
        }
        
        mock_get_token.return_value = "test-access-token"
        
        mock_build_invoice.return_value = {
            "contact": {"ContactID": "xero-contact-123"},
            "line_items": [
                {
                    "Description": "Test Product",
                    "Quantity": 1,
                    "UnitAmount": 100.0,
                    "AccountCode": "200",
                    "TaxType": "OUTPUT",
                },
                {
                    "Description": "Order ORD123 placed by Test User",
                    "Quantity": 0,
                    "UnitAmount": 0,
                    "AccountCode": "200",
                    "TaxType": "OUTPUT",
                }
            ],
            "line_amount_type": "Exclusive",
            "reference": "PO-12345",
            "context": {
                "order": {"order_number": "ORD123"},
                "user_name": "Test User",
            }
        }
        
        mock_get_company.return_value = {
            "id": 1,
            "name": "Test Company",
            "xero_id": "xero-contact-123",
        }
        
        mock_create_event.return_value = {
            "id": 456,
            "status": "in_progress",
        }
        
        # Setup HTTP client mock
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"Invoices": [{"InvoiceNumber": "INV-001"}]}'
        mock_response.headers = {}
        
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        # Call the function
        result = await xero_service.send_order_to_xero(
            order_number="ORD123",
            company_id=1,
            user_name="Test User",
        )
        
        # Assertions
        assert result["status"] == "succeeded"
        assert result["order_number"] == "ORD123"
        assert result["company_id"] == 1
        assert result["invoice_number"] == "INV-001"
        
        # Verify the API was called
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        
        # Check the invoice payload
        invoice_payload = call_args[1]["json"]
        assert invoice_payload["Type"] == "ACCREC"
        assert invoice_payload["Reference"] == "PO-12345"
        assert invoice_payload["Status"] == "DRAFT"
        assert len(invoice_payload["LineItems"]) == 2
        
        # Check that user info line was included
        user_line = invoice_payload["LineItems"][1]
        assert "Test User" in user_line["Description"]
        assert "ORD123" in user_line["Description"]


@pytest.mark.anyio("asyncio")
async def test_send_order_to_xero_module_disabled():
    """Test that sending is skipped when Xero module is disabled."""
    
    with patch("app.services.xero.modules_service.get_module") as mock_get_module:
        mock_get_module.return_value = {
            "enabled": False,
            "settings": {},
        }
        
        result = await xero_service.send_order_to_xero(
            order_number="ORD123",
            company_id=1,
            user_name="Test User",
        )
        
        assert result["status"] == "skipped"
        assert result["reason"] == "Xero module is disabled"


@pytest.mark.anyio("asyncio")
async def test_send_order_to_xero_missing_configuration():
    """Test that sending is skipped when Xero configuration is incomplete."""
    
    with patch("app.services.xero.modules_service.get_module") as mock_get_module:
        mock_get_module.return_value = {
            "enabled": True,
            "settings": {
                "client_id": "test-id",
                # Missing other required fields
            },
        }
        
        result = await xero_service.send_order_to_xero(
            order_number="ORD123",
            company_id=1,
            user_name="Test User",
        )
        
        assert result["status"] == "skipped"
        assert result["reason"] == "Xero module not fully configured"
        assert "missing" in result
