"""Tests for subscription price change functionality."""
import pytest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.repositories import billing_contacts as billing_contacts_repo
from app.services import subscription_price_changes


@pytest.mark.anyio("asyncio")
async def test_add_billing_contact(monkeypatch):
    """Test adding a billing contact."""
    mock_execute = AsyncMock()
    mock_fetch_one = AsyncMock(return_value={
        "id": 1,
        "company_id": 5,
        "user_id": 10,
        "created_at": "2025-01-01",
        "email": "test@example.com",
        "first_name": "John",
        "last_name": "Doe",
    })
    
    with patch("app.repositories.billing_contacts.db.execute", mock_execute), \
         patch("app.repositories.billing_contacts.db.fetch_one", mock_fetch_one):
        contact = await billing_contacts_repo.add_billing_contact(5, 10)
        
        assert contact["user_id"] == 10
        assert contact["company_id"] == 5
        assert contact["email"] == "test@example.com"
        mock_execute.assert_called_once()


@pytest.mark.anyio("asyncio")
async def test_remove_billing_contact(monkeypatch):
    """Test removing a billing contact."""
    mock_execute = AsyncMock()
    
    with patch("app.repositories.billing_contacts.db.execute", mock_execute):
        await billing_contacts_repo.remove_billing_contact(5, 10)
        mock_execute.assert_called_once()


@pytest.mark.anyio("asyncio")
async def test_list_billing_contacts_for_company(monkeypatch):
    """Test listing billing contacts for a company."""
    mock_fetch_all = AsyncMock(return_value=[
        {
            "id": 1,
            "company_id": 5,
            "user_id": 10,
            "created_at": "2025-01-01",
            "email": "test1@example.com",
            "first_name": "John",
            "last_name": "Doe",
        },
        {
            "id": 2,
            "company_id": 5,
            "user_id": 11,
            "created_at": "2025-01-01",
            "email": "test2@example.com",
            "first_name": "Jane",
            "last_name": "Smith",
        },
    ])
    
    with patch("app.repositories.billing_contacts.db.fetch_all", mock_fetch_all):
        contacts = await billing_contacts_repo.list_billing_contacts_for_company(5)
        
        assert len(contacts) == 2
        assert contacts[0]["email"] == "test1@example.com"
        assert contacts[1]["email"] == "test2@example.com"


@pytest.mark.anyio("asyncio")
async def test_get_products_with_pending_price_changes(monkeypatch):
    """Test getting products with pending price changes."""
    today = date.today()
    mock_fetch_all = AsyncMock(return_value=[
        {
            "id": 1,
            "name": "Product A",
            "sku": "SKU-A",
            "subscription_category_id": 10,
            "category_name": "Cloud Services",
            "current_price": Decimal("10.00"),
            "current_vip_price": Decimal("9.00"),
            "current_buy_price": Decimal("8.00"),
            "scheduled_price": Decimal("12.00"),
            "scheduled_vip_price": Decimal("10.00"),
            "scheduled_buy_price": Decimal("9.00"),
            "price_change_date": today,
        }
    ])
    
    with patch("app.services.subscription_price_changes.db.fetch_all", mock_fetch_all):
        products = await subscription_price_changes.get_products_with_pending_price_changes()
        
        assert len(products) == 1
        assert products[0]["name"] == "Product A"
        assert products[0]["scheduled_price"] == Decimal("12.00")
        assert products[0]["subscription_category_id"] == 10


@pytest.mark.anyio("asyncio")
async def test_mark_product_price_change_notified(monkeypatch):
    """Test marking a product's price change as notified."""
    mock_execute = AsyncMock()
    
    with patch("app.services.subscription_price_changes.db.execute", mock_execute):
        await subscription_price_changes.mark_product_price_change_notified(1)
        
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args
        assert "price_change_notified = 1" in call_args[0][0]


@pytest.mark.anyio("asyncio")
async def test_get_companies_with_subscriptions_for_products(monkeypatch):
    """Test getting companies with subscriptions for products."""
    mock_fetch_all = AsyncMock(return_value=[
        {"product_id": 1, "customer_id": 5},
        {"product_id": 1, "customer_id": 6},
        {"product_id": 2, "customer_id": 5},
    ])
    
    with patch("app.services.subscription_price_changes.db.fetch_all", mock_fetch_all):
        result = await subscription_price_changes.get_companies_with_subscriptions_for_products([1, 2])
        
        assert 1 in result
        assert 2 in result
        assert 5 in result[1]
        assert 6 in result[1]
        assert 5 in result[2]
