"""Tests for license usage history tracking."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_record_usage_if_changed_first_entry():
    """First snapshot for a license is always recorded."""
    from app.repositories import licenses as repo

    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute:

        mock_fetch_one.return_value = None  # No prior history

        result = await repo.record_usage_if_changed(license_id=1, count=10, allocated=3)

        assert result is True
        mock_execute.assert_awaited_once()
        call_args = mock_execute.call_args
        assert "INSERT INTO license_usage_history" in call_args[0][0]
        assert call_args[0][1] == (1, 10, 3)


@pytest.mark.asyncio
async def test_record_usage_if_changed_same_values_skips():
    """No new row written when count and allocated have not changed."""
    from app.repositories import licenses as repo

    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute:

        mock_fetch_one.return_value = {"count": 10, "allocated": 3}

        result = await repo.record_usage_if_changed(license_id=1, count=10, allocated=3)

        assert result is False
        mock_execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_usage_if_changed_count_changed():
    """New row written when count changes."""
    from app.repositories import licenses as repo

    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute:

        mock_fetch_one.return_value = {"count": 10, "allocated": 3}

        result = await repo.record_usage_if_changed(license_id=1, count=20, allocated=3)

        assert result is True
        mock_execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_usage_if_changed_allocated_changed():
    """New row written when allocated count changes."""
    from app.repositories import licenses as repo

    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute:

        mock_fetch_one.return_value = {"count": 10, "allocated": 3}

        result = await repo.record_usage_if_changed(license_id=1, count=10, allocated=5)

        assert result is True
        mock_execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_usage_if_changed_both_changed():
    """New row written when both count and allocated change."""
    from app.repositories import licenses as repo

    with patch.object(repo.db, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch.object(repo.db, "execute", new_callable=AsyncMock) as mock_execute:

        mock_fetch_one.return_value = {"count": 5, "allocated": 2}

        result = await repo.record_usage_if_changed(license_id=1, count=15, allocated=8)

        assert result is True
        mock_execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_usage_history_returns_list():
    """get_usage_history returns a list of dicts with expected keys."""
    from app.repositories import licenses as repo

    fake_rows = [
        {"id": 1, "license_id": 1, "count": 10, "allocated": 3, "recorded_at": datetime(2024, 1, 1)},
        {"id": 2, "license_id": 1, "count": 20, "allocated": 5, "recorded_at": datetime(2024, 6, 1)},
    ]

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = fake_rows

        history = await repo.get_usage_history(1)

        assert len(history) == 2
        assert history[0]["count"] == 10
        assert history[0]["allocated"] == 3
        assert history[1]["count"] == 20
        assert history[1]["allocated"] == 5
        assert "recorded_at" in history[0]


@pytest.mark.asyncio
async def test_get_usage_history_empty():
    """get_usage_history returns empty list when no rows exist."""
    from app.repositories import licenses as repo

    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
        mock_fetch_all.return_value = []

        history = await repo.get_usage_history(99)

        assert history == []
