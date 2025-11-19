from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_scheduler_syncs_unifi_talk_recordings_success():
    task = {"id": 5, "command": "sync_unifi_talk_recordings"}

    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    with patch(
        "app.services.scheduler.modules_service.trigger_module",
        new_callable=AsyncMock,
    ) as mock_trigger, patch(
        "app.services.scheduler.scheduled_tasks_repo.record_task_run",
        new_callable=AsyncMock,
    ) as mock_record, patch(
        "app.services.scheduler.db.acquire_lock",
    ) as mock_lock:
        mock_trigger.return_value = {
            "status": "ok",
            "downloaded": 3,
            "skipped": 1,
        }
        mock_lock.return_value.__aenter__.return_value = True

        await scheduler._run_task(task)

        mock_trigger.assert_awaited_once_with("unifi-talk", {}, background=False)
        assert mock_record.call_args.kwargs["status"] == "succeeded"
        details = mock_record.call_args.kwargs["details"]
        assert "downloaded" in (details or "")


@pytest.mark.asyncio
async def test_scheduler_syncs_unifi_talk_recordings_handles_errors():
    task = {"id": 6, "command": "sync_unifi_talk_recordings"}

    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()

    with patch(
        "app.services.scheduler.modules_service.trigger_module",
        new_callable=AsyncMock,
    ) as mock_trigger, patch(
        "app.services.scheduler.scheduled_tasks_repo.record_task_run",
        new_callable=AsyncMock,
    ) as mock_record, patch(
        "app.services.scheduler.db.acquire_lock",
    ) as mock_lock:
        mock_trigger.return_value = {"status": "error", "error": "missing"}
        mock_lock.return_value.__aenter__.return_value = True

        await scheduler._run_task(task)

        mock_trigger.assert_awaited_once_with("unifi-talk", {}, background=False)
        assert mock_record.call_args.kwargs["status"] == "failed"
        assert "missing" in (mock_record.call_args.kwargs["details"] or "")
