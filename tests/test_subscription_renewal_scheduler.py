"""Regression checks for the administrator-controlled renewal schedule."""

from pathlib import Path
import asyncio
from unittest.mock import AsyncMock, patch

from app.main import TASK_COMMAND_LABELS
from app.services.scheduler import SchedulerService


def test_subscription_renewal_command_is_available_to_admins() -> None:
    assert (
        TASK_COMMAND_LABELS["process_subscription_renewals"]
        == "Process subscription renewals"
    )


def test_subscription_renewal_migration_seeds_one_global_task() -> None:
    sql = Path("migrations/372_subscription_renewal_scheduled_task.sql").read_text()

    assert "process_subscription_renewals" in sql
    assert "WHERE NOT EXISTS" in sql
    assert "company_id" not in sql.partition("SELECT")[0]
    assert "'0 2 * * *'" in sql


def test_global_task_dispatches_subscription_renewal_processing() -> None:
    asyncio.run(_run_global_task_dispatch_case())


async def _run_global_task_dispatch_case() -> None:
    service = SchedulerService()
    task = {
        "id": 372,
        "command": "process_subscription_renewals",
        "company_id": None,
        "cron": "0 2 * * *",
    }
    summary = {"reminder_count": 1, "invoice_count": 1, "error_count": 0}

    with (
        patch("app.services.scheduler.db.acquire_lock") as lock,
        patch(
            "app.services.scheduler.scheduled_tasks_repo.has_run_since",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.services.scheduler.subscription_renewals.create_renewal_invoices_for_date",
            new_callable=AsyncMock,
            return_value=summary,
        ) as process,
        patch(
            "app.services.scheduler.scheduled_tasks_repo.record_task_run",
            new_callable=AsyncMock,
        ) as record_run,
    ):
        lock.return_value.__aenter__.return_value = True
        await service._run_task(task)

    process.assert_awaited_once()
    assert record_run.await_args.kwargs["status"] == "succeeded"
    assert '"reminder_count": 1' in record_run.await_args.kwargs["details"]
