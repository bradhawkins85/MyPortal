"""Tests for subscription Xero invoice integration."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.subscription_changes import apply_subscription_addition


@pytest.mark.asyncio
async def test_apply_subscription_addition_with_charge_creates_xero_invoice(monkeypatch):
    """Test that applying a subscription addition with a charge creates a Xero invoice."""
    # Setup mock data
    subscription_id = str(uuid4())
    change_request_id = str(uuid4())
    
    mock_subscription = {
        "id": subscription_id,
        "quantity": 5,
        "unit_price": Decimal("120.00"),
        "end_date": date.today() + timedelta(days=180),
        "customer_id": 1,
        "product_id": 100,
        "product_name": "Test Product",
    }
    
    mock_change_request = {
        "id": change_request_id,
        "subscription_id": subscription_id,
        "change_type": "addition",
        "quantity_change": 3,
        "prorated_charge": Decimal("180.00"),
        "status": "pending",
    }
    
    mock_xero_result = {
        "status": "succeeded",
        "invoice_number": "INV-001",
        "subscription_id": subscription_id,
        "customer_id": 1,
    }
    
    # Mock repository functions
    async def mock_get_subscription(sub_id):
        return mock_subscription
    
    async def mock_list_pending_changes(sub_id):
        return []
    
    async def mock_update_subscription(sub_id, **kwargs):
        pass
    
    async def mock_create_change_request(**kwargs):
        return mock_change_request
    
    async def mock_apply_change_request(cr_id):
        pass
    
    async def mock_update_xero_invoice_number(cr_id, invoice_num):
        pass
    
    # Mock Xero service
    async def mock_send_subscription_charge_to_xero(*args, **kwargs):
        return mock_xero_result
    
    # Apply all patches
    import app.repositories.subscriptions as subscriptions_repo
    import app.repositories.subscription_change_requests as change_requests_repo
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", mock_get_subscription)
    monkeypatch.setattr(subscriptions_repo, "update_subscription", mock_update_subscription)
    monkeypatch.setattr(change_requests_repo, "list_pending_changes_for_subscription", mock_list_pending_changes)
    monkeypatch.setattr(change_requests_repo, "create_change_request", mock_create_change_request)
    monkeypatch.setattr(change_requests_repo, "apply_change_request", mock_apply_change_request)
    monkeypatch.setattr(change_requests_repo, "update_xero_invoice_number", mock_update_xero_invoice_number)
    
    with patch("app.services.xero.send_subscription_charge_to_xero", new=mock_send_subscription_charge_to_xero):
        # Apply the subscription addition
        result = await apply_subscription_addition(
            subscription_id=subscription_id,
            quantity_to_add=3,
            requested_by=1,
            notes="Test addition",
        )
    
    # Verify the result
    assert result["subscription"]["quantity"] == 5  # Original quantity from mock
    assert result["prorated_charge"] > Decimal("0")
    assert result["xero_result"] is not None
    assert result["xero_result"]["status"] == "succeeded"
    assert result["xero_result"]["invoice_number"] == "INV-001"


@pytest.mark.asyncio
async def test_apply_subscription_addition_with_no_charge_skips_xero(monkeypatch):
    """Test that applying a subscription addition with no charge skips Xero invoice creation."""
    # Setup mock data
    subscription_id = str(uuid4())
    change_request_id = str(uuid4())
    
    # Mock pending decrease that offsets the addition
    mock_pending_changes = [
        {
            "id": str(uuid4()),
            "subscription_id": subscription_id,
            "change_type": "decrease",
            "quantity_change": 2,
            "prorated_charge": None,
            "status": "pending",
        }
    ]
    
    mock_subscription = {
        "id": subscription_id,
        "quantity": 5,
        "unit_price": Decimal("120.00"),
        "end_date": date.today() + timedelta(days=180),
        "customer_id": 1,
        "product_id": 100,
        "product_name": "Test Product",
    }
    
    mock_change_request = {
        "id": change_request_id,
        "subscription_id": subscription_id,
        "change_type": "addition",
        "quantity_change": 1,
        "prorated_charge": Decimal("0.00"),  # No charge
        "status": "pending",
    }
    
    # Mock repository functions
    async def mock_get_subscription(sub_id):
        return mock_subscription
    
    async def mock_list_pending_changes(sub_id):
        return mock_pending_changes
    
    async def mock_update_subscription(sub_id, **kwargs):
        pass
    
    async def mock_create_change_request(**kwargs):
        return mock_change_request
    
    async def mock_apply_change_request(cr_id):
        pass
    
    # Mock Xero service (should not be called)
    mock_xero_send = AsyncMock()
    
    # Apply all patches
    import app.repositories.subscriptions as subscriptions_repo
    import app.repositories.subscription_change_requests as change_requests_repo
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", mock_get_subscription)
    monkeypatch.setattr(subscriptions_repo, "update_subscription", mock_update_subscription)
    monkeypatch.setattr(change_requests_repo, "list_pending_changes_for_subscription", mock_list_pending_changes)
    monkeypatch.setattr(change_requests_repo, "create_change_request", mock_create_change_request)
    monkeypatch.setattr(change_requests_repo, "apply_change_request", mock_apply_change_request)
    
    with patch("app.services.xero.send_subscription_charge_to_xero", new=mock_xero_send):
        # Apply the subscription addition
        result = await apply_subscription_addition(
            subscription_id=subscription_id,
            quantity_to_add=1,
            requested_by=1,
            notes="Test addition with no charge",
        )
    
    # Verify the result
    assert result["subscription"]["quantity"] == 5  # Original quantity from mock
    assert result["prorated_charge"] == Decimal("0.00")
    assert result["xero_result"] is None  # Should not create Xero invoice
    
    # Verify Xero service was not called
    mock_xero_send.assert_not_called()


@pytest.mark.asyncio
async def test_apply_subscription_addition_xero_failure_does_not_block(monkeypatch):
    """Test that Xero invoice failure doesn't prevent subscription addition."""
    # Setup mock data
    subscription_id = str(uuid4())
    change_request_id = str(uuid4())
    
    mock_subscription = {
        "id": subscription_id,
        "quantity": 5,
        "unit_price": Decimal("120.00"),
        "end_date": date.today() + timedelta(days=180),
        "customer_id": 1,
        "product_id": 100,
        "product_name": "Test Product",
    }
    
    mock_change_request = {
        "id": change_request_id,
        "subscription_id": subscription_id,
        "change_type": "addition",
        "quantity_change": 3,
        "prorated_charge": Decimal("180.00"),
        "status": "pending",
    }
    
    mock_xero_result = {
        "status": "failed",
        "error": "HTTP 400",
        "subscription_id": subscription_id,
        "customer_id": 1,
    }
    
    # Mock repository functions
    async def mock_get_subscription(sub_id):
        return mock_subscription
    
    async def mock_list_pending_changes(sub_id):
        return []
    
    async def mock_update_subscription(sub_id, **kwargs):
        pass
    
    async def mock_create_change_request(**kwargs):
        return mock_change_request
    
    async def mock_apply_change_request(cr_id):
        pass
    
    # Mock Xero service with failure
    async def mock_send_subscription_charge_to_xero(*args, **kwargs):
        return mock_xero_result
    
    # Apply all patches
    import app.repositories.subscriptions as subscriptions_repo
    import app.repositories.subscription_change_requests as change_requests_repo
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", mock_get_subscription)
    monkeypatch.setattr(subscriptions_repo, "update_subscription", mock_update_subscription)
    monkeypatch.setattr(change_requests_repo, "list_pending_changes_for_subscription", mock_list_pending_changes)
    monkeypatch.setattr(change_requests_repo, "create_change_request", mock_create_change_request)
    monkeypatch.setattr(change_requests_repo, "apply_change_request", mock_apply_change_request)
    
    with patch("app.services.xero.send_subscription_charge_to_xero", new=mock_send_subscription_charge_to_xero):
        # Apply the subscription addition
        result = await apply_subscription_addition(
            subscription_id=subscription_id,
            quantity_to_add=3,
            requested_by=1,
            notes="Test addition",
        )
    
    # Verify the subscription was still added despite Xero failure
    assert result["subscription"]["quantity"] == 5  # Original quantity from mock
    assert result["prorated_charge"] > Decimal("0")
    assert result["xero_result"] is not None
    assert result["xero_result"]["status"] == "failed"
    assert "error" in result["xero_result"]
