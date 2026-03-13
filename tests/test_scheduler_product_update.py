"""Tests for the 'update_products' scheduler command.

The command must use the stock feed already cached by a prior ``update_stock_feed``
run.  It must NOT download the XML feed itself.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_update_products_does_not_download_feed():
    """The update_products task only calls update_products_from_feed, not update_stock_feed."""
    task = {"id": 99, "command": "update_products"}

    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    stock_feed_called = False

    async def fake_update_stock_feed():
        nonlocal stock_feed_called
        stock_feed_called = True

    async def fake_update_products_from_feed():
        pass

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

    assert not stock_feed_called, (
        "update_products must not download the XML feed; "
        "it should use the feed already provided by update_stock_feed"
    )


@pytest.mark.asyncio
async def test_update_products_calls_update_products_from_feed():
    """The update_products task calls update_products_from_feed to sync products."""
    task = {"id": 100, "command": "update_products"}

    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    products_called = False

    async def fake_update_products_from_feed():
        nonlocal products_called
        products_called = True

    with patch(
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
        "update_products_from_feed must be called by the update_products task"
    )
