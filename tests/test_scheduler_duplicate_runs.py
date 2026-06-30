import asyncio
from unittest.mock import AsyncMock, patch

import pytest


DUPLICATE_GUARDED_COMMANDS = [
    (
        "generate_invoice",
        "app.services.scheduler.invoice_generator_service.generate_invoice",
    ),
    (
        "sync_to_xero",
        "app.services.scheduler.xero_service.sync_company",
    ),
    (
        "sync_to_xero_auto_send",
        "app.services.scheduler.xero_service.sync_company",
    ),
]


@pytest.mark.parametrize(("command", "service_path"), DUPLICATE_GUARDED_COMMANDS)
def test_scheduled_task_skips_duplicate_fire_within_same_minute(command, service_path):
    asyncio.run(_run_duplicate_skip_case(command, service_path))


async def _run_duplicate_skip_case(command, service_path):
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    task = {"id": 123, "command": command, "company_id": 86, "cron": "6 2 * * *"}

    with (
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
        patch("app.services.scheduler.scheduled_tasks_repo.has_run_since", new_callable=AsyncMock, return_value=True) as mock_has_run_since,
        patch(service_path, new_callable=AsyncMock) as mock_service,
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", new_callable=AsyncMock) as mock_record,
    ):
        mock_lock.return_value.__aenter__.return_value = True

        await scheduler._run_task(task)

    mock_has_run_since.assert_awaited_once()
    mock_service.assert_not_awaited()
    mock_record.assert_not_awaited()


def test_run_now_bypasses_duplicate_fire_debounce():
    asyncio.run(_run_force_restart_case())


async def _run_force_restart_case():
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    task = {"id": 123, "command": "generate_invoice", "company_id": 86, "cron": "6 2 * * *"}

    with (
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
        patch("app.services.scheduler.scheduled_tasks_repo.has_run_since", new_callable=AsyncMock, return_value=True) as mock_has_run_since,
        patch("app.services.scheduler.invoice_generator_service.generate_invoice", new_callable=AsyncMock, return_value={"status": "skipped"}) as mock_generate,
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", new_callable=AsyncMock) as mock_record,
    ):
        mock_lock.return_value.__aenter__.return_value = True

        await scheduler._run_task(task, force_restart=True)

    mock_has_run_since.assert_not_awaited()
    mock_generate.assert_awaited_once_with(86)
    mock_record.assert_awaited_once()
