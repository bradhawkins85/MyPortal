"""Tests for the 'update_products' scheduler command.

The command must refresh the stock feed *before* running the product sync so
that ``opt_accessori`` values are always up-to-date when cross-sell associations
are resolved.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, call, patch

import pytest


@pytest.mark.asyncio
async def test_update_products_refreshes_stock_feed_first():
    """The update_products task calls update_stock_feed before update_products_from_feed."""
    task = {"id": 99, "command": "update_products"}

    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    call_order: list[str] = []

    async def fake_update_stock_feed():
        call_order.append("update_stock_feed")

    async def fake_update_products_from_feed():
        call_order.append("update_products_from_feed")

    with patch(
        "app.services.scheduler.products_service.update_stock_feed",
        side_effect=fake_update_stock_feed,
    ), patch(
        "app.services.scheduler.products_service.update_products_from_feed",
        side_effect=fake_update_products_from_feed,
    ), patch(
        "app.services.scheduler.scheduled_tasks_repo.record_task_run",
        new_callable=AsyncMock,
    ), patch(
        "app.services.scheduler.db.acquire_lock",
    ) as mock_lock:
        mock_lock.return_value.__aenter__.return_value = True

        await scheduler._run_task(task)

    assert call_order == ["update_stock_feed", "update_products_from_feed"], (
        "update_stock_feed must be called before update_products_from_feed so that "
        "opt_accessori values are populated before cross-sell resolution runs"
    )


@pytest.mark.asyncio
async def test_update_products_continues_when_stock_feed_refresh_fails():
    """If update_stock_feed raises, update_products_from_feed still runs."""
    task = {"id": 100, "command": "update_products"}

    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    products_called = False

    async def fake_update_stock_feed():
        raise ValueError("STOCK_FEED_URL is not configured")

    async def fake_update_products_from_feed():
        nonlocal products_called
        products_called = True

    with patch(
        "app.services.scheduler.products_service.update_stock_feed",
        side_effect=fake_update_stock_feed,
    ), patch(
        "app.services.scheduler.products_service.update_products_from_feed",
        side_effect=fake_update_products_from_feed,
    ), patch(
        "app.services.scheduler.scheduled_tasks_repo.record_task_run",
        new_callable=AsyncMock,
    ), patch(
        "app.services.scheduler.db.acquire_lock",
    ) as mock_lock:
        mock_lock.return_value.__aenter__.return_value = True

        await scheduler._run_task(task)

    assert products_called, (
        "update_products_from_feed must still run even when update_stock_feed fails, "
        "so that products can be synced from the cached stock feed"
    )
