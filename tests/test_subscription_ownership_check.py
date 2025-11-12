"""Tests for subscription repository functions."""
import pytest

from app.core.database import db
from app.repositories import subscriptions as subscriptions_repo


@pytest.fixture(autouse=True)
def mock_db_lifecycle(monkeypatch):
    """Mock database lifecycle methods."""
    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    async def fake_run_migrations():
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)


@pytest.mark.asyncio
async def test_get_active_subscription_product_ids_with_active_subscriptions(monkeypatch):
    """Test getting active subscription product IDs returns correct set."""
    
    async def fake_fetch_all(query, params):
        # Simulate database returning active subscriptions
        return [
            {"product_id": 123},
            {"product_id": 456},
            {"product_id": 789},
        ]
    
    monkeypatch.setattr(db, "fetch_all", fake_fetch_all)
    
    result = await subscriptions_repo.get_active_subscription_product_ids(customer_id=1)
    
    assert isinstance(result, set)
    assert result == {123, 456, 789}


@pytest.mark.asyncio
async def test_get_active_subscription_product_ids_with_no_subscriptions(monkeypatch):
    """Test getting active subscription product IDs returns empty set when none exist."""
    
    async def fake_fetch_all(query, params):
        # Simulate database returning no subscriptions
        return []
    
    monkeypatch.setattr(db, "fetch_all", fake_fetch_all)
    
    result = await subscriptions_repo.get_active_subscription_product_ids(customer_id=1)
    
    assert isinstance(result, set)
    assert result == set()


@pytest.mark.asyncio
async def test_get_active_subscription_product_ids_with_duplicates(monkeypatch):
    """Test that duplicate product IDs are handled correctly (should not happen but test defensively)."""
    
    async def fake_fetch_all(query, params):
        # Simulate database returning duplicate product IDs (edge case)
        return [
            {"product_id": 123},
            {"product_id": 123},
            {"product_id": 456},
        ]
    
    monkeypatch.setattr(db, "fetch_all", fake_fetch_all)
    
    result = await subscriptions_repo.get_active_subscription_product_ids(customer_id=1)
    
    assert isinstance(result, set)
    assert result == {123, 456}
    assert len(result) == 2  # Set should eliminate duplicates
