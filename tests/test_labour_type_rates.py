"""Tests for labour type rates functionality."""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import labour_types as labour_types_service
from app.services import xero as xero_service


@pytest.mark.asyncio
async def test_create_labour_type_with_rate():
    """Test creating a labour type with a rate."""
    mock_repo = MagicMock()
    
    # Mock get_labour_type_by_code to return None (no existing)
    with patch("app.services.labour_types.labour_types_repo") as mock_labour_types_repo:
        mock_labour_types_repo.get_labour_type_by_code = AsyncMock(return_value=None)
        mock_labour_types_repo.create_labour_type = AsyncMock(return_value={
            "id": 1,
            "code": "REMOTE",
            "name": "Remote Support",
            "rate": 95.00,
            "created_at": None,
            "updated_at": None,
        })
        
        result = await labour_types_service.create_labour_type(
            code="REMOTE",
            name="Remote Support",
            rate=95.00
        )
        
        assert result["code"] == "REMOTE"
        assert result["name"] == "Remote Support"
        assert result["rate"] == 95.00
        
        # Verify create was called with the rate
        mock_labour_types_repo.create_labour_type.assert_called_once_with(
            code="REMOTE",
            name="Remote Support",
            rate=95.00
        )


@pytest.mark.asyncio
async def test_update_labour_type_with_rate():
    """Test updating a labour type's rate."""
    with patch("app.services.labour_types.labour_types_repo") as mock_labour_types_repo:
        mock_labour_types_repo.get_labour_type_by_code = AsyncMock(return_value=None)
        mock_labour_types_repo.update_labour_type = AsyncMock(return_value={
            "id": 1,
            "code": "REMOTE",
            "name": "Remote Support",
            "rate": 105.00,
            "created_at": None,
            "updated_at": None,
        })
        
        result = await labour_types_service.update_labour_type(
            labour_type_id=1,
            rate=105.00
        )
        
        assert result["rate"] == 105.00
        
        # Verify update was called with the rate
        mock_labour_types_repo.update_labour_type.assert_called_once_with(
            1,
            rate=105.00
        )


@pytest.mark.asyncio
async def test_replace_labour_types_with_rates():
    """Test replacing labour types including rates."""
    definitions = [
        {"id": 1, "code": "REMOTE", "name": "Remote Support", "rate": 95.00},
        {"id": 2, "code": "ONSITE", "name": "On-site Support", "rate": 150.00},
        {"id": None, "code": "PHONE", "name": "Phone Support", "rate": 75.00},
    ]
    
    with patch("app.services.labour_types.labour_types_repo") as mock_labour_types_repo:
        mock_labour_types_repo.replace_labour_types = AsyncMock(return_value=[
            {"id": 1, "code": "REMOTE", "name": "Remote Support", "rate": 95.00},
            {"id": 2, "code": "ONSITE", "name": "On-site Support", "rate": 150.00},
            {"id": 3, "code": "PHONE", "name": "Phone Support", "rate": 75.00},
        ])
        
        result = await labour_types_service.replace_labour_types(definitions)
        
        assert len(result) == 3
        assert result[0]["rate"] == 95.00
        assert result[1]["rate"] == 150.00
        assert result[2]["rate"] == 75.00


@pytest.mark.asyncio
async def test_xero_uses_local_rate_first():
    """Test that Xero billing uses local labour type rate.
    
    Local rate is the only source for labour-specific rates now.
    Xero item rates are no longer fetched.
    """
    
    # Mock ticket with labour type that has a local rate
    mock_ticket = {
        "id": 1,
        "company_id": 1,
        "status": "closed",
        "subject": "Test ticket",
    }
    
    mock_replies = [
        {
            "id": 1,
            "minutes_spent": 60,
            "is_billable": True,
            "labour_type_code": "REMOTE",
            "labour_type_name": "Remote Support",
            "labour_type_rate": 95.00,  # Local rate set
        }
    ]
    
    mock_company = {
        "id": 1,
        "name": "Test Company",
        "xero_contact_id": "test-contact-id",
    }
    
    # Mock fetchers
    async def fetch_ticket(ticket_id: int):
        return mock_ticket
    
    async def fetch_replies(ticket_id: int):
        return mock_replies
    
    async def fetch_company(company_id: int):
        return mock_company
    
    # Xero item rates (should be ignored since local rate is set)
    xero_item_rates = {
        "REMOTE": Decimal("120.00")  # Different rate in Xero
    }
    
    # Build invoice
    invoices = await xero_service.build_ticket_invoices(
        ticket_ids=[1],
        account_code="200",
        tax_type="OUTPUT2",
        line_amount_type="Exclusive",
        hourly_rate=Decimal("80.00"),  # Default rate
        allowed_statuses=["closed"],
        description_template="Ticket {ticket_id}",
        reference_prefix="Support",
        xero_item_rates=xero_item_rates,
        fetch_ticket=fetch_ticket,
        fetch_replies=fetch_replies,
        fetch_company=fetch_company,
    )
    
    # Should use local rate (95.00), not Xero rate (120.00) or default (80.00)
    assert len(invoices) == 1
    invoice = invoices[0]
    assert len(invoice["line_items"]) == 1
    line_item = invoice["line_items"][0]
    
    # Rate should be 95.00 (local rate)
    assert line_item["UnitAmount"] == 95.00
    assert line_item["Quantity"] == 1.0  # 60 minutes = 1 hour


@pytest.mark.asyncio
async def test_xero_falls_back_to_default_when_no_local_rate():
    """Test that Xero billing falls back to default rate when no local rate is set.
    
    Note: Xero item rates are no longer fetched. Only local labour type rates
    are used, with fallback to default hourly rate.
    """
    
    mock_ticket = {
        "id": 1,
        "company_id": 1,
        "status": "closed",
        "subject": "Test ticket",
    }
    
    mock_replies = [
        {
            "id": 1,
            "minutes_spent": 60,
            "is_billable": True,
            "labour_type_code": "REMOTE",
            "labour_type_name": "Remote Support",
            "labour_type_rate": None,  # No local rate
        }
    ]
    
    mock_company = {
        "id": 1,
        "name": "Test Company",
        "xero_contact_id": "test-contact-id",
    }
    
    async def fetch_ticket(ticket_id: int):
        return mock_ticket
    
    async def fetch_replies(ticket_id: int):
        return mock_replies
    
    async def fetch_company(company_id: int):
        return mock_company
    
    # Xero item rates parameter is now ignored (deprecated)
    xero_item_rates = {
        "REMOTE": Decimal("120.00")
    }
    
    # Build invoice
    invoices = await xero_service.build_ticket_invoices(
        ticket_ids=[1],
        account_code="200",
        tax_type="OUTPUT2",
        line_amount_type="Exclusive",
        hourly_rate=Decimal("80.00"),
        allowed_statuses=["closed"],
        description_template="Ticket {ticket_id}",
        reference_prefix="Support",
        xero_item_rates=xero_item_rates,  # This is now ignored
        fetch_ticket=fetch_ticket,
        fetch_replies=fetch_replies,
        fetch_company=fetch_company,
    )
    
    # Should use default rate (80.00), NOT Xero rate (120.00)
    assert len(invoices) == 1
    invoice = invoices[0]
    line_item = invoice["line_items"][0]
    
    # Rate should be 80.00 (default rate, Xero rate is no longer fetched)
    assert line_item["UnitAmount"] == 80.00


@pytest.mark.asyncio
async def test_xero_falls_back_to_default_rate():
    """Test that Xero billing falls back to default rate when no local or Xero rate."""
    
    mock_ticket = {
        "id": 1,
        "company_id": 1,
        "status": "closed",
        "subject": "Test ticket",
    }
    
    mock_replies = [
        {
            "id": 1,
            "minutes_spent": 60,
            "is_billable": True,
            "labour_type_code": "REMOTE",
            "labour_type_name": "Remote Support",
            "labour_type_rate": None,  # No local rate
        }
    ]
    
    mock_company = {
        "id": 1,
        "name": "Test Company",
        "xero_contact_id": "test-contact-id",
    }
    
    async def fetch_ticket(ticket_id: int):
        return mock_ticket
    
    async def fetch_replies(ticket_id: int):
        return mock_replies
    
    async def fetch_company(company_id: int):
        return mock_company
    
    # No Xero item rates available
    xero_item_rates = {}
    
    # Build invoice
    invoices = await xero_service.build_ticket_invoices(
        ticket_ids=[1],
        account_code="200",
        tax_type="OUTPUT2",
        line_amount_type="Exclusive",
        hourly_rate=Decimal("80.00"),
        allowed_statuses=["closed"],
        description_template="Ticket {ticket_id}",
        reference_prefix="Support",
        xero_item_rates=xero_item_rates,
        fetch_ticket=fetch_ticket,
        fetch_replies=fetch_replies,
        fetch_company=fetch_company,
    )
    
    # Should use default rate (80.00)
    assert len(invoices) == 1
    invoice = invoices[0]
    line_item = invoice["line_items"][0]
    
    # Rate should be 80.00 (default rate)
    assert line_item["UnitAmount"] == 80.00
