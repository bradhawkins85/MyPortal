"""Tests for the scheduled audit log pruning job.

Covers:
- The job is registered by _ensure_monitoring_jobs
- The job calls prune_audit_logs with the configured retention value
- retention_days == 0 skips pruning
- The prune function uses batched deletion
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest


# ─── Scheduler job registration ────────────────────────────────────────────────


def test_audit_log_prune_job_is_registered():
    """_ensure_monitoring_jobs registers the 'audit-log-prune' job."""
    asyncio.run(_run_registered_case())


async def _run_registered_case():
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    scheduler._started = True

    with (
        patch.object(
            scheduler._scheduler,
            "get_job",
            side_effect=lambda jid: None if jid == "audit-log-prune" else object(),
        ),
        patch.object(
            scheduler._scheduler,
            "add_job",
            wraps=scheduler._scheduler.add_job,
        ) as mock_add_job,
    ):
        await scheduler._ensure_monitoring_jobs()

    added_ids = [
        call.kwargs.get("id") for call in mock_add_job.call_args_list
    ]
    assert "audit-log-prune" in added_ids


def test_audit_log_prune_job_calls_prune_with_configured_value():
    """_run_audit_log_prune calls prune_audit_logs with settings.audit_retention_days."""
    asyncio.run(_run_configured_value_case())


async def _run_configured_value_case():
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    with (
        patch("app.services.scheduler.get_settings") as mock_settings,
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
        patch(
            "app.services.scheduler.audit_logs_repo.prune_audit_logs",
            new_callable=AsyncMock,
            return_value=42,
        ) as mock_prune,
        patch("app.services.scheduler.log_info"),
    ):
        mock_settings.return_value.audit_retention_days = 365
        mock_lock.return_value.__aenter__.return_value = True

        await scheduler._run_audit_log_prune()

    mock_prune.assert_awaited_once_with(retention_days=365)


def test_audit_log_prune_skips_when_retention_zero():
    """_run_audit_log_prune returns early when audit_retention_days == 0."""
    asyncio.run(_run_retention_zero_case())


async def _run_retention_zero_case():
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    with (
        patch("app.services.scheduler.get_settings") as mock_settings,
        patch(
            "app.services.scheduler.audit_logs_repo.prune_audit_logs",
            new_callable=AsyncMock,
        ) as mock_prune,
    ):
        mock_settings.return_value.audit_retention_days = 0

        await scheduler._run_audit_log_prune()

    mock_prune.assert_not_awaited()


def test_audit_log_prune_skips_when_lock_not_acquired():
    """_run_audit_log_prune does nothing when the distributed lock is not acquired."""
    asyncio.run(_run_lock_not_acquired_case())


async def _run_lock_not_acquired_case():
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    with (
        patch("app.services.scheduler.get_settings") as mock_settings,
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
        patch(
            "app.services.scheduler.audit_logs_repo.prune_audit_logs",
            new_callable=AsyncMock,
        ) as mock_prune,
    ):
        mock_settings.return_value.audit_retention_days = 365
        mock_lock.return_value.__aenter__.return_value = False

        await scheduler._run_audit_log_prune()

    mock_prune.assert_not_awaited()


# ─── prune_audit_logs function ─────────────────────────────────────────────────


def test_prune_audit_logs_returns_zero_when_retention_zero():
    """prune_audit_logs is a no-op when retention_days is 0."""
    asyncio.run(_run_prune_zero_case())


async def _run_prune_zero_case():
    from app.repositories.audit_logs import prune_audit_logs

    with patch("app.repositories.audit_logs.db") as mock_db:
        result = await prune_audit_logs(retention_days=0)

    assert result == 0
    mock_db.execute_rowcount.assert_not_called()


def test_prune_audit_logs_returns_zero_when_retention_negative():
    """prune_audit_logs is a no-op when retention_days is negative."""
    asyncio.run(_run_prune_negative_case())


async def _run_prune_negative_case():
    from app.repositories.audit_logs import prune_audit_logs

    with patch("app.repositories.audit_logs.db") as mock_db:
        result = await prune_audit_logs(retention_days=-5)

    assert result == 0
    mock_db.execute_rowcount.assert_not_called()


def test_prune_audit_logs_deletes_in_single_batch():
    """prune_audit_logs deletes all rows in one batch when fewer than batch size."""
    asyncio.run(_run_prune_single_batch_case())


async def _run_prune_single_batch_case():
    from app.repositories.audit_logs import prune_audit_logs

    with patch("app.repositories.audit_logs.db") as mock_db:
        mock_db.execute_rowcount = AsyncMock(return_value=100)

        result = await prune_audit_logs(retention_days=365)

    assert result == 100
    assert mock_db.execute_rowcount.await_count == 1
    sql = mock_db.execute_rowcount.call_args[0][0]
    assert "%s" in sql
    assert "LIMIT %s" in sql
    params = mock_db.execute_rowcount.call_args[0][1]
    assert isinstance(params[0], datetime)


def test_prune_audit_logs_deletes_in_multiple_batches():
    """prune_audit_logs loops until fewer than batch_size rows remain."""
    asyncio.run(_run_prune_multiple_batches_case())


async def _run_prune_multiple_batches_case():
    from app.repositories.audit_logs import prune_audit_logs, _PRUNE_BATCH_SIZE

    call_count = 0

    async def fake_execute_rowcount(sql, params):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return _PRUNE_BATCH_SIZE
        return 50

    with patch("app.repositories.audit_logs.db") as mock_db:
        mock_db.execute_rowcount = fake_execute_rowcount

        result = await prune_audit_logs(retention_days=365)

    assert result == _PRUNE_BATCH_SIZE * 2 + 50
    assert call_count == 3


def test_prune_audit_logs_cutoff_date_is_correct():
    """The cutoff passed to the query is now - retention_days."""
    asyncio.run(_run_prune_cutoff_case())


async def _run_prune_cutoff_case():
    from app.repositories.audit_logs import prune_audit_logs

    with patch("app.repositories.audit_logs.db") as mock_db:
        mock_db.execute_rowcount = AsyncMock(return_value=0)

        await prune_audit_logs(retention_days=30)

    params = mock_db.execute_rowcount.call_args[0][1]
    cutoff = params[0]
    expected_cutoff = datetime.utcnow() - timedelta(days=30)
    delta = abs((cutoff - expected_cutoff).total_seconds())
    assert delta < 2
