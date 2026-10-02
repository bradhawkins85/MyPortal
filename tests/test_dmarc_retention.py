"""Tests for the scheduled DMARC report retention / pruning job.

Covers:
- The job is registered by _ensure_monitoring_jobs
- The job calls prune_reports with the configured retention value
- retention_days <= 0 skips pruning
- The distributed lock short-circuits when not acquired
- prune_reports is a no-op for retention_days <= 0
- prune_reports drains both report roots in batches and returns the total
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

# ─── Scheduler job registration ────────────────────────────────────────────────


def test_dmarc_report_prune_job_is_registered():
    """_ensure_monitoring_jobs registers the 'dmarc-report-prune' job."""
    asyncio.run(_run_registered_case())


async def _run_registered_case():
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    scheduler._started = True

    with (
        patch.object(
            scheduler._scheduler,
            "get_job",
            side_effect=lambda jid: None if jid == "dmarc-report-prune" else object(),
        ),
        patch.object(
            scheduler._scheduler,
            "add_job",
            wraps=scheduler._scheduler.add_job,
        ) as mock_add_job,
    ):
        await scheduler._ensure_monitoring_jobs()

    added_ids = [call.kwargs.get("id") for call in mock_add_job.call_args_list]
    assert "dmarc-report-prune" in added_ids


def test_dmarc_report_prune_job_calls_prune_with_configured_value():
    """_run_dmarc_report_prune calls prune_reports with settings.dmarc_retention_days."""
    asyncio.run(_run_configured_value_case())


async def _run_configured_value_case():
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    with (
        patch("app.services.scheduler.get_settings") as mock_settings,
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
        patch(
            "app.services.scheduler.dmarc_repo.prune_reports",
            new_callable=AsyncMock,
            return_value=42,
        ) as mock_prune,
        patch("app.services.scheduler.log_info"),
    ):
        mock_settings.return_value.dmarc_retention_days = 365
        mock_lock.return_value.__aenter__.return_value = True

        await scheduler._run_dmarc_report_prune()

    mock_prune.assert_awaited_once_with(retention_days=365)


def test_dmarc_report_prune_skips_when_retention_zero():
    """_run_dmarc_report_prune returns early when dmarc_retention_days <= 0."""
    asyncio.run(_run_retention_zero_case())


async def _run_retention_zero_case():
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    with (
        patch("app.services.scheduler.get_settings") as mock_settings,
        patch(
            "app.services.scheduler.dmarc_repo.prune_reports",
            new_callable=AsyncMock,
        ) as mock_prune,
    ):
        mock_settings.return_value.dmarc_retention_days = 0

        await scheduler._run_dmarc_report_prune()

    mock_prune.assert_not_awaited()


def test_dmarc_report_prune_skips_when_lock_not_acquired():
    """_run_dmarc_report_prune does nothing when the distributed lock is not acquired."""
    asyncio.run(_run_lock_not_acquired_case())


async def _run_lock_not_acquired_case():
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    with (
        patch("app.services.scheduler.get_settings") as mock_settings,
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
        patch(
            "app.services.scheduler.dmarc_repo.prune_reports",
            new_callable=AsyncMock,
        ) as mock_prune,
        patch("app.services.scheduler.log_info"),
    ):
        mock_settings.return_value.dmarc_retention_days = 365
        mock_lock.return_value.__aenter__.return_value = False

        await scheduler._run_dmarc_report_prune()

    mock_prune.assert_not_awaited()


# ─── prune_reports function ─────────────────────────────────────────────────────


def test_prune_reports_returns_zero_when_retention_zero():
    """prune_reports is a no-op when retention_days is 0."""
    asyncio.run(_run_prune_zero_case())


async def _run_prune_zero_case():
    from app.repositories import dmarc

    with patch("app.repositories.dmarc.db") as mock_db:
        result = await dmarc.prune_reports(retention_days=0)

    assert result == 0
    mock_db.execute_rowcount.assert_not_called()


def test_prune_reports_returns_zero_when_retention_negative():
    """prune_reports is a no-op when retention_days is negative."""
    asyncio.run(_run_prune_negative_case())


async def _run_prune_negative_case():
    from app.repositories import dmarc

    with patch("app.repositories.dmarc.db") as mock_db:
        result = await dmarc.prune_reports(retention_days=-5)

    assert result == 0
    mock_db.execute_rowcount.assert_not_called()


def test_prune_reports_deletes_single_batch_per_table():
    """prune_reports prunes both report roots, one batch each when under the limit."""
    asyncio.run(_run_prune_single_batch_case())


async def _run_prune_single_batch_case():
    from app.repositories import dmarc

    with patch("app.repositories.dmarc.db") as mock_db:
        mock_db.execute_rowcount = AsyncMock(return_value=100)

        result = await dmarc.prune_reports(retention_days=365)

    # One batch per root (forensic + aggregate); each returned fewer than the
    # batch size so the per-table loop breaks after a single call.
    assert result == 200
    assert mock_db.execute_rowcount.await_count == 2
    first_sql = mock_db.execute_rowcount.call_args_list[0][0][0]
    second_sql = mock_db.execute_rowcount.call_args_list[1][0][0]
    assert "dmarc_forensic_reports" in first_sql
    assert "dmarc_reports" in second_sql
    assert "LIMIT %s" in first_sql
    params = mock_db.execute_rowcount.call_args[0][1]
    assert isinstance(params[0], datetime)


def test_prune_reports_deletes_in_multiple_batches():
    """prune_reports drains each root until a batch returns fewer rows."""
    asyncio.run(_run_prune_multiple_batches_case())


async def _run_prune_multiple_batches_case():
    from app.repositories import dmarc
    from app.repositories.dmarc import _PRUNE_BATCH_SIZE

    counts = defaultdict(int)

    async def fake_execute_rowcount(sql, params):
        key = "forensic" if "dmarc_forensic_reports" in sql else "reports"
        counts[key] += 1
        return _PRUNE_BATCH_SIZE if counts[key] == 1 else 50

    with patch("app.repositories.dmarc.db") as mock_db:
        mock_db.execute_rowcount = fake_execute_rowcount

        result = await dmarc.prune_reports(retention_days=365)

    # Each root fills one full batch, then a partial batch breaks its loop.
    assert result == 2 * _PRUNE_BATCH_SIZE + 100
    assert counts["forensic"] == 2
    assert counts["reports"] == 2


def test_prune_reports_cutoff_date_is_correct():
    """The cutoff passed to the query is now - retention_days."""
    asyncio.run(_run_prune_cutoff_case())


async def _run_prune_cutoff_case():
    from app.repositories import dmarc

    with patch("app.repositories.dmarc.db") as mock_db:
        mock_db.execute_rowcount = AsyncMock(return_value=0)

        await dmarc.prune_reports(retention_days=30)

    params = mock_db.execute_rowcount.call_args[0][1]
    cutoff = params[0]
    expected_cutoff = datetime.utcnow() - timedelta(days=30)
    delta = abs((cutoff - expected_cutoff).total_seconds())
    assert delta < 2
