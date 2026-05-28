from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_sync_to_xero_partial_result_marks_task_failed():
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    task = {"id": 77, "command": "sync_to_xero", "company_id": 123}

    with patch(
        "app.services.scheduler.xero_service.sync_company",
        new=AsyncMock(return_value={"status": "partial", "failed_count": 1}),
    ) as mock_sync_company, patch(
        "app.services.scheduler.scheduled_tasks_repo.record_task_run",
        new_callable=AsyncMock,
    ) as mock_record_task_run, patch(
        "app.services.scheduler.db.acquire_lock",
    ) as mock_lock:
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(task)

    mock_sync_company.assert_awaited_once_with(123)
    record_kwargs = mock_record_task_run.await_args.kwargs
    assert record_kwargs["status"] == "failed"
    details_payload = json.loads(record_kwargs["details"])
    assert details_payload["status"] == "partial"
    assert details_payload["failed_count"] == 1
