"""Tests that sync_mailboxes is called automatically during sync_m365_data."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_task(company_id: int = 1) -> dict[str, Any]:
    return {
        "id": 99,
        "company_id": company_id,
        "command": "sync_m365_data",
    }


def _make_staff_summary() -> Any:
    s = MagicMock()
    s.created = 0
    s.updated = 0
    s.skipped = 0
    s.total = 0
    return s


@pytest.mark.asyncio
async def test_sync_m365_data_calls_sync_mailboxes():
    """sync_mailboxes is called during the sync_m365_data scheduled task."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    sync_mailboxes_calls: list[int] = []

    async def fake_sync_mailboxes(company_id: int) -> int:
        sync_mailboxes_calls.append(company_id)
        return 5

    with (
        patch("app.services.scheduler.m365_service.sync_company_licenses", new_callable=AsyncMock),
        patch(
            "app.services.scheduler.staff_importer.import_m365_contacts_for_company",
            new_callable=AsyncMock,
            return_value=_make_staff_summary(),
        ),
        patch("app.services.scheduler.m365_service.sync_mailboxes", side_effect=fake_sync_mailboxes),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", new_callable=AsyncMock),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task(company_id=42))

    assert sync_mailboxes_calls == [42], "sync_mailboxes must be called with the task's company_id"


@pytest.mark.asyncio
async def test_sync_m365_data_details_includes_mailboxes_synced():
    """The task details JSON produced by sync_m365_data includes mailboxes_synced."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded_details: list[str] = []

    async def fake_record_run(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded_details.append(details or "")

    with (
        patch("app.services.scheduler.m365_service.sync_company_licenses", new_callable=AsyncMock),
        patch(
            "app.services.scheduler.staff_importer.import_m365_contacts_for_company",
            new_callable=AsyncMock,
            return_value=_make_staff_summary(),
        ),
        patch("app.services.scheduler.m365_service.sync_mailboxes", new_callable=AsyncMock, return_value=7),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record_run),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task(company_id=1))

    assert recorded_details, "record_task_run should have been called"
    result = json.loads(recorded_details[-1])
    assert result.get("mailboxes_synced") == 7
    assert result.get("mailbox_sync_error") is None


@pytest.mark.asyncio
async def test_sync_m365_data_continues_if_sync_mailboxes_fails():
    """If sync_mailboxes raises, the overall task still succeeds with mailboxes_synced=0."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded_statuses: list[str] = []
    recorded_details: list[str] = []

    async def fake_record_run(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded_statuses.append(status)
        recorded_details.append(details or "")

    with (
        patch("app.services.scheduler.m365_service.sync_company_licenses", new_callable=AsyncMock),
        patch(
            "app.services.scheduler.staff_importer.import_m365_contacts_for_company",
            new_callable=AsyncMock,
            return_value=_make_staff_summary(),
        ),
        patch(
            "app.services.scheduler.m365_service.sync_mailboxes",
            AsyncMock(side_effect=Exception("Graph error")),
        ),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record_run),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task(company_id=1))

    assert recorded_statuses, "record_task_run should have been called"
    # The outermost task status should still be "succeeded" even though mailbox sync failed
    assert recorded_statuses[-1] == "succeeded", (
        f"Task should succeed when sync_mailboxes fails; got status={recorded_statuses[-1]!r}"
    )
    result = json.loads(recorded_details[-1])
    assert result.get("mailboxes_synced") == 0
    assert result.get("mailbox_sync_error") == "Graph error"
