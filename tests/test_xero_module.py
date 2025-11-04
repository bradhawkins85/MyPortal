from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import modules as modules_service
from app.services import xero as xero_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_coerce_settings_xero_preserves_secrets():
    existing = {
        "settings": {
            "client_id": "existing-id",
            "client_secret": "super-secret",
            "refresh_token": "refresh-token",
            "default_hourly_rate": "120.00",
            "billable_statuses": ["open", "pending"],
            "line_item_description_template": "Ticket {ticket_id}: {ticket_subject}",
        }
    }
    payload = {
        "client_id": "new-id",
        "client_secret": "********",
        "default_hourly_rate": "175",
        "billable_statuses": "resolved, Closed",
        "line_item_description_template": " Ticket #{ticket_id} - {ticket_subject} ",
    }
    result = modules_service._coerce_settings("xero", payload, existing)
    assert result["client_secret"] == "super-secret"
    assert result["refresh_token"] == "refresh-token"
    assert result["client_id"] == "new-id"
    assert result["default_hourly_rate"] == "175.00"
    assert result["billable_statuses"] == ["resolved", "closed"]
    assert result["line_item_description_template"] == "Ticket #{ticket_id} - {ticket_subject}"


def test_coerce_settings_xero_includes_company_name():
    """Test that company_name field is properly handled in Xero settings."""
    existing = {
        "settings": {
            "client_id": "existing-id",
            "client_secret": "super-secret",
            "refresh_token": "refresh-token",
            "tenant_id": "existing-tenant",
            "company_name": "Old Company Name",
        }
    }
    payload = {
        "company_name": "New Company Name",
    }
    result = modules_service._coerce_settings("xero", payload, existing)
    assert result["company_name"] == "New Company Name"
    assert result["tenant_id"] == "existing-tenant"
    # Verify secrets are preserved
    assert result["client_secret"] == "super-secret"
    assert result["refresh_token"] == "refresh-token"


@pytest.mark.anyio("asyncio")
async def test_build_ticket_invoices_groups_billable_minutes():
    async def fake_fetch_ticket(ticket_id: int):
        return {
            "id": ticket_id,
            "company_id": 1,
            "subject": f"Ticket {ticket_id}",
            "status": "resolved",
        }

    async def fake_fetch_replies(ticket_id: int):
        if ticket_id == 1:
            return [
                {"minutes_spent": 30, "is_billable": True},
                {"minutes_spent": 15, "is_billable": False},
            ]
        return [
            {"minutes_spent": 45, "is_billable": False},
            {"minutes_spent": 10, "is_billable": False},
        ]

    async def fake_fetch_company(company_id: int):
        return {"id": company_id, "name": "Acme Corp", "xero_id": "abc-123"}

    invoices = await xero_service.build_ticket_invoices(
        [1, "2"],
        hourly_rate=Decimal("150"),
        account_code="400",
        tax_type="OUTPUT",
        line_amount_type="Exclusive",
        reference_prefix="Support",
        fetch_ticket=fake_fetch_ticket,
        fetch_replies=fake_fetch_replies,
        fetch_company=fake_fetch_company,
    )

    assert len(invoices) == 1
    invoice = invoices[0]
    assert invoice["context"]["total_billable_minutes"] == 30
    assert invoice["line_items"][0]["UnitAmount"] == 150.0
    assert invoice["line_items"][0]["Quantity"] == 0.5


@pytest.mark.anyio("asyncio")
async def test_build_ticket_invoices_respects_status_filters_and_templates():
    invoice_day = date(2024, 5, 1)

    async def fake_fetch_ticket(ticket_id: int):
        status = "resolved" if ticket_id == 1 else "open"
        return {
            "id": ticket_id,
            "company_id": 1,
            "subject": f"Ticket {ticket_id}",
            "status": status,
        }

    async def fake_fetch_replies(ticket_id: int):
        return [
            {
                "minutes_spent": 30,
                "is_billable": True,
                "labour_type_code": "REMOTE",
                "labour_type_name": "Remote",
            }
        ]

    async def fake_fetch_company(company_id: int):
        return {"id": company_id, "name": "Acme Corp", "xero_id": "abc-123"}

    existing_invoice = {
        "type": "ACCREC",
        "contact": {"Name": "Acme Corp"},
        "line_items": [
            {"Description": "Existing", "Quantity": 1.0, "UnitAmount": 100.0, "AccountCode": "400"}
        ],
        "line_amount_type": "Exclusive",
        "reference": "Support — Tickets 100",
        "context": {
            "company": {"id": 1, "name": "Acme Corp", "xero_id": "abc-123"},
            "tickets": [
                {
                    "id": 100,
                    "subject": "Earlier",
                    "billable_minutes": 60,
                    "status": "resolved",
                    "labour_groups": [],
                }
            ],
            "total_billable_minutes": 60,
            "invoice_date": invoice_day.isoformat(),
        },
    }
    invoice_map: dict[tuple[int, date], dict] = {(1, invoice_day): existing_invoice}

    invoices = await xero_service.build_ticket_invoices(
        [1, 2],
        hourly_rate=Decimal("150"),
        account_code="400",
        tax_type=None,
        line_amount_type="Exclusive",
        reference_prefix="Support",
        allowed_statuses=["resolved"],
        description_template="Ticket #{ticket_id} - {ticket_subject} - {labour_name}",
        invoice_date=invoice_day,
        existing_invoice_map=invoice_map,
        fetch_ticket=fake_fetch_ticket,
        fetch_replies=fake_fetch_replies,
        fetch_company=fake_fetch_company,
    )

    assert invoices == [existing_invoice]
    assert len(existing_invoice["line_items"]) == 2
    assert existing_invoice["line_items"][-1]["Description"] == "Ticket #1 - Ticket 1 - Remote"
    assert existing_invoice["context"]["total_billable_minutes"] == 90
    assert existing_invoice["context"]["tickets"][-1]["id"] == 1
    assert existing_invoice["context"]["invoice_date"] == invoice_day.isoformat()
    assert existing_invoice["reference"] == "Support — Tickets 100, 1"


@pytest.mark.anyio("asyncio")
async def test_build_order_invoice_returns_payload_with_context():
    async def fake_fetch_summary(order_number: str, company_id: int):
        return {"order_number": order_number, "status": "placed"}

    async def fake_fetch_items(order_number: str, company_id: int):
        return [
            {
                "quantity": 2,
                "price": Decimal("19.99"),
                "product_name": "Widget",
                "sku": "WID-1",
            }
        ]

    async def fake_fetch_company(company_id: int):
        return {"id": company_id, "name": "Acme Corp", "xero_id": "xyz-789"}

    invoice = await xero_service.build_order_invoice(
        "SO-100",
        1,
        account_code="400",
        tax_type=None,
        line_amount_type="Exclusive",
        fetch_summary=fake_fetch_summary,
        fetch_items=fake_fetch_items,
        fetch_company=fake_fetch_company,
    )

    assert invoice is not None
    assert invoice["line_items"][0]["Quantity"] == 2
    assert invoice["context"]["order"]["order_number"] == "SO-100"
    assert invoice["context"]["company"]["xero_id"] == "xyz-789"


@pytest.mark.anyio("asyncio")
async def test_discover_xero_tenant_id_from_connections_api():
    """Test that _discover_xero_tenant_id makes correct API calls and matches tenant by name."""
    
    # Mock httpx.AsyncClient
    with patch("app.services.modules.httpx.AsyncClient") as mock_client_class:
        # Create a mock client instance
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        # Mock the token response - use MagicMock for sync methods
        mock_token_response = MagicMock()
        mock_token_response.json.return_value = {
            "access_token": "test_access_token",
            "token_type": "Bearer",
            "expires_in": 1800,
        }
        mock_token_response.raise_for_status.return_value = None
        
        # Mock the connections response
        mock_connections_response = MagicMock()
        mock_connections_response.json.return_value = [
            {
                "id": "connection-1",
                "tenantId": "wrong-tenant-id",
                "tenantName": "Other Company",
                "tenantType": "ORGANISATION",
            },
            {
                "id": "connection-2",
                "tenantId": "correct-tenant-id",
                "tenantName": "Test Company Name",
                "tenantType": "ORGANISATION",
            },
            {
                "id": "connection-3",
                "tenantId": "another-tenant-id",
                "tenantName": "Another Company",
                "tenantType": "ORGANISATION",
            },
        ]
        mock_connections_response.raise_for_status.return_value = None
        
        # Set up the mock client to return the appropriate responses
        mock_client.post.return_value = mock_token_response
        mock_client.get.return_value = mock_connections_response
        
        # Call the function
        tenant_id = await modules_service._discover_xero_tenant_id(
            client_id="test_client_id",
            client_secret="test_client_secret",
            refresh_token="test_refresh_token",
            company_name="Test Company Name",
        )
        
        # Verify the result
        assert tenant_id == "correct-tenant-id"
        
        # Verify the token request was made correctly
        mock_client.post.assert_called_once()
        post_call = mock_client.post.call_args
        assert post_call[0][0] == "https://identity.xero.com/connect/token"
        assert post_call[1]["data"]["grant_type"] == "refresh_token"
        assert post_call[1]["data"]["refresh_token"] == "test_refresh_token"
        assert post_call[1]["auth"] == ("test_client_id", "test_client_secret")
        
        # Verify the connections request was made correctly
        mock_client.get.assert_called_once()
        get_call = mock_client.get.call_args
        assert get_call[0][0] == "https://api.xero.com/connections"
        assert get_call[1]["headers"]["Authorization"] == "Bearer test_access_token"


@pytest.mark.anyio("asyncio")
async def test_discover_xero_tenant_id_case_insensitive_matching():
    """Test that tenant name matching is case-insensitive."""
    
    with patch("app.services.modules.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        mock_token_response = MagicMock()
        mock_token_response.json.return_value = {"access_token": "test_token"}
        mock_token_response.raise_for_status.return_value = None
        
        mock_connections_response = MagicMock()
        mock_connections_response.json.return_value = [
            {
                "tenantId": "matching-tenant",
                "tenantName": "MY COMPANY NAME",
            },
        ]
        mock_connections_response.raise_for_status.return_value = None
        
        mock_client.post.return_value = mock_token_response
        mock_client.get.return_value = mock_connections_response
        
        # Test with lowercase company name
        tenant_id = await modules_service._discover_xero_tenant_id(
            client_id="test_id",
            client_secret="test_secret",
            refresh_token="test_token",
            company_name="my company name",
        )
        
        assert tenant_id == "matching-tenant"


@pytest.mark.anyio("asyncio")
async def test_discover_xero_tenant_id_no_match():
    """Test that None is returned when no matching tenant is found."""
    
    with patch("app.services.modules.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        mock_token_response = MagicMock()
        mock_token_response.json.return_value = {"access_token": "test_token"}
        mock_token_response.raise_for_status.return_value = None
        
        mock_connections_response = MagicMock()
        mock_connections_response.json.return_value = [
            {"tenantId": "tenant-1", "tenantName": "Different Company"},
            {"tenantId": "tenant-2", "tenantName": "Another Company"},
        ]
        mock_connections_response.raise_for_status.return_value = None
        
        mock_client.post.return_value = mock_token_response
        mock_client.get.return_value = mock_connections_response
        
        tenant_id = await modules_service._discover_xero_tenant_id(
            client_id="test_id",
            client_secret="test_secret",
            refresh_token="test_token",
            company_name="Nonexistent Company",
        )
        
        assert tenant_id is None


@pytest.mark.anyio("asyncio")
async def test_discover_xero_tenant_id_missing_credentials():
    """Test that None is returned when required credentials are missing."""
    
    # Missing company_name
    tenant_id = await modules_service._discover_xero_tenant_id(
        client_id="test_id",
        client_secret="test_secret",
        refresh_token="test_token",
        company_name="",
    )
    assert tenant_id is None
    
    # Missing refresh_token
    tenant_id = await modules_service._discover_xero_tenant_id(
        client_id="test_id",
        client_secret="test_secret",
        refresh_token="",
        company_name="Test Company",
    )
    assert tenant_id is None
