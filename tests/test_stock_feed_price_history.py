"""Tests for DBP price history tracking in app/repositories/stock_feed.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


# ---------------------------------------------------------------------------
# Tests for get_recent_dbp_trends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_recent_dbp_trends_empty_skus():
    """Empty SKU list returns empty dict without a DB query."""
    from app.repositories import stock_feed as repo

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        result = await repo.get_recent_dbp_trends([])
        assert result == {}
        mock_fetch_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_recent_dbp_trends_price_increased():
    """Returns 'up' when the most recent change within 30 days is an increase."""
    from app.repositories import stock_feed as repo

    now = datetime.now(timezone.utc)
    fake_rows = [
        {"sku": "SKU001", "dbp": "12.00", "recorded_at": now - timedelta(days=5)},
        {"sku": "SKU001", "dbp": "10.00", "recorded_at": now - timedelta(days=60)},
    ]

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = fake_rows
        result = await repo.get_recent_dbp_trends(["SKU001"])

    assert result == {"SKU001": "up"}


@pytest.mark.asyncio
async def test_get_recent_dbp_trends_price_decreased():
    """Returns 'down' when the most recent change within 30 days is a decrease."""
    from app.repositories import stock_feed as repo

    now = datetime.now(timezone.utc)
    fake_rows = [
        {"sku": "SKU001", "dbp": "8.00", "recorded_at": now - timedelta(days=10)},
        {"sku": "SKU001", "dbp": "10.00", "recorded_at": now - timedelta(days=60)},
    ]

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = fake_rows
        result = await repo.get_recent_dbp_trends(["SKU001"])

    assert result == {"SKU001": "down"}


@pytest.mark.asyncio
async def test_get_recent_dbp_trends_change_older_than_30_days():
    """Returns None when the most recent record is older than 30 days."""
    from app.repositories import stock_feed as repo

    now = datetime.now(timezone.utc)
    fake_rows = [
        {"sku": "SKU001", "dbp": "12.00", "recorded_at": now - timedelta(days=31)},
        {"sku": "SKU001", "dbp": "10.00", "recorded_at": now - timedelta(days=90)},
    ]

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = fake_rows
        result = await repo.get_recent_dbp_trends(["SKU001"])

    assert result == {"SKU001": None}


@pytest.mark.asyncio
async def test_get_recent_dbp_trends_only_one_record():
    """Returns None when there is only one price record (no comparison possible)."""
    from app.repositories import stock_feed as repo

    now = datetime.now(timezone.utc)
    fake_rows = [
        {"sku": "SKU001", "dbp": "10.00", "recorded_at": now - timedelta(days=1)},
    ]

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = fake_rows
        result = await repo.get_recent_dbp_trends(["SKU001"])

    assert result == {"SKU001": None}


@pytest.mark.asyncio
async def test_get_recent_dbp_trends_no_history():
    """Returns no entry for a SKU with no price history records."""
    from app.repositories import stock_feed as repo

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = []
        result = await repo.get_recent_dbp_trends(["SKU001"])

    assert result.get("SKU001") is None


@pytest.mark.asyncio
async def test_get_recent_dbp_trends_same_price_no_arrow():
    """Returns None when current and previous DBP are the same."""
    from app.repositories import stock_feed as repo

    now = datetime.now(timezone.utc)
    fake_rows = [
        {"sku": "SKU001", "dbp": "10.00", "recorded_at": now - timedelta(days=5)},
        {"sku": "SKU001", "dbp": "10.00", "recorded_at": now - timedelta(days=60)},
    ]

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = fake_rows
        result = await repo.get_recent_dbp_trends(["SKU001"])

    assert result == {"SKU001": None}


@pytest.mark.asyncio
async def test_get_recent_dbp_trends_multiple_skus():
    """Handles multiple SKUs in one call."""
    from app.repositories import stock_feed as repo

    now = datetime.now(timezone.utc)
    fake_rows = [
        # SKU001: increased
        {"sku": "SKU001", "dbp": "15.00", "recorded_at": now - timedelta(days=3)},
        {"sku": "SKU001", "dbp": "10.00", "recorded_at": now - timedelta(days=60)},
        # SKU002: decreased
        {"sku": "SKU002", "dbp": "5.00", "recorded_at": now - timedelta(days=7)},
        {"sku": "SKU002", "dbp": "8.00", "recorded_at": now - timedelta(days=45)},
        # SKU003: old change only
        {"sku": "SKU003", "dbp": "20.00", "recorded_at": now - timedelta(days=45)},
        {"sku": "SKU003", "dbp": "18.00", "recorded_at": now - timedelta(days=90)},
    ]

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = fake_rows
        result = await repo.get_recent_dbp_trends(["SKU001", "SKU002", "SKU003"])

    assert result["SKU001"] == "up"
    assert result["SKU002"] == "down"
    assert result["SKU003"] is None

