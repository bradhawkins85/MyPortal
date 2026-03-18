"""Tests for DBP price history tracking in app/repositories/stock_feed.py."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_record_price_if_changed_first_entry():
    """First price for a SKU is always recorded."""
    from app.repositories import stock_feed as repo

    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute:

        mock_fetch_one.return_value = None  # No prior history

        result = await repo.record_price_if_changed("SKU001", Decimal("10.00"))

        assert result is True
        mock_execute.assert_awaited_once()
        call_args = mock_execute.call_args
        assert "INSERT INTO stock_feed_price_history" in call_args[0][0]
        assert call_args[0][1] == ("SKU001", Decimal("10.00"))


@pytest.mark.asyncio
async def test_record_price_if_changed_same_price_skips():
    """No new row written when price has not changed."""
    from app.repositories import stock_feed as repo

    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute:

        mock_fetch_one.return_value = {"dbp": "10.00"}

        result = await repo.record_price_if_changed("SKU001", Decimal("10.00"))

        assert result is False
        mock_execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_price_if_changed_price_changed():
    """New row written when price changes."""
    from app.repositories import stock_feed as repo

    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute:

        mock_fetch_one.return_value = {"dbp": "10.00"}

        result = await repo.record_price_if_changed("SKU001", Decimal("12.50"))

        assert result is True
        mock_execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_price_if_changed_null_to_value():
    """Null -> value transition is recorded."""
    from app.repositories import stock_feed as repo

    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute:

        mock_fetch_one.return_value = {"dbp": None}

        result = await repo.record_price_if_changed("SKU001", Decimal("10.00"))

        assert result is True
        mock_execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_price_if_changed_value_to_null():
    """Value -> None transition is recorded."""
    from app.repositories import stock_feed as repo

    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute:

        mock_fetch_one.return_value = {"dbp": "10.00"}

        result = await repo.record_price_if_changed("SKU001", None)

        assert result is True
        mock_execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_price_if_changed_both_null_skips():
    """None -> None transition is not recorded."""
    from app.repositories import stock_feed as repo

    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute:

        mock_fetch_one.return_value = {"dbp": None}

        result = await repo.record_price_if_changed("SKU001", None)

        assert result is False
        mock_execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_price_history_returns_list():
    """get_price_history returns a list of dicts with expected keys."""
    from datetime import datetime
    from app.repositories import stock_feed as repo

    fake_rows = [
        {"id": 1, "sku": "SKU001", "dbp": "9.99", "recorded_at": datetime(2024, 1, 1, 0, 0, 0)},
        {"id": 2, "sku": "SKU001", "dbp": "10.50", "recorded_at": datetime(2024, 6, 1, 0, 0, 0)},
    ]

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = fake_rows

        history = await repo.get_price_history("SKU001")

        assert len(history) == 2
        assert history[0]["sku"] == "SKU001"
        assert history[0]["dbp"] == Decimal("9.99")
        assert history[1]["dbp"] == Decimal("10.50")
        assert "recorded_at" in history[0]


@pytest.mark.asyncio
async def test_get_price_history_null_dbp():
    """get_price_history handles NULL dbp values."""
    from datetime import datetime
    from app.repositories import stock_feed as repo

    fake_rows = [
        {"id": 1, "sku": "SKU001", "dbp": None, "recorded_at": datetime(2024, 1, 1, 0, 0, 0)},
    ]

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = fake_rows

        history = await repo.get_price_history("SKU001")

        assert len(history) == 1
        assert history[0]["dbp"] is None


@pytest.mark.asyncio
async def test_get_price_history_empty():
    """get_price_history returns empty list when no rows exist."""
    from app.repositories import stock_feed as repo

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = []

        history = await repo.get_price_history("UNKNOWN_SKU")

        assert history == []
