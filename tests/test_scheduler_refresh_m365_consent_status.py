"""Tests for the refresh_m365_consent_status scheduled task command."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


def _make_task(company_id: int | None = 1) -> dict[str, Any]:
    return {"id": 20, "company_id": company_id, "command": "refresh_m365_consent_status"}


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


def test_refresh_m365_consent_status_in_commands_by_module():
    """refresh_m365_consent_status is registered under the m365 module."""
    from app.services.scheduler import COMMANDS_BY_MODULE

    assert "refresh_m365_consent_status" in COMMANDS_BY_MODULE["m365"]


def test_refresh_m365_consent_status_label_in_main():
    """TASK_COMMAND_LABELS contains a human-readable label for the command."""
    from app.main import TASK_COMMAND_LABELS

    assert "refresh_m365_consent_status" in TASK_COMMAND_LABELS
    assert TASK_COMMAND_LABELS["refresh_m365_consent_status"]


# ---------------------------------------------------------------------------
# Handler behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_m365_consent_status_calls_check_permissions():
    """Handler calls check_enterprise_app_permissions with the task company_id."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    calls: list[int] = []

    async def fake_check(cid: int) -> list[dict]:
        calls.append(cid)
        return [{"name": "Microsoft Graph", "all_ok": True}]

    with (
        patch(
            "app.services.scheduler.m365_service.check_enterprise_app_permissions",
            side_effect=fake_check,
        ),
        patch(
            "app.services.scheduler.scheduled_tasks_repo.record_task_run",
            new_callable=AsyncMock,
        ),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task(company_id=42))

    assert calls == [42]


@pytest.mark.asyncio
async def test_refresh_m365_consent_status_records_success_all_ok():
    """Handler records succeeded with all_ok=True when all apps pass."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    with (
        patch(
            "app.services.scheduler.m365_service.check_enterprise_app_permissions",
            new_callable=AsyncMock,
            return_value=[
                {"name": "Microsoft Graph", "all_ok": True},
                {"name": "Exchange Online", "all_ok": True},
            ],
        ),
        patch(
            "app.services.scheduler.scheduled_tasks_repo.record_task_run",
            side_effect=fake_record,
        ),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task())

    assert recorded[-1]["status"] == "succeeded"
    data = json.loads(recorded[-1]["details"])
    assert data["all_ok"] is True
    assert data["apps_checked"] == 2
    assert data["company_id"] == 1


@pytest.mark.asyncio
async def test_refresh_m365_consent_status_records_success_not_all_ok():
    """Handler records succeeded (not failed) even when consent is not up to date."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    with (
        patch(
            "app.services.scheduler.m365_service.check_enterprise_app_permissions",
            new_callable=AsyncMock,
            return_value=[{"name": "Microsoft Graph", "all_ok": False}],
        ),
        patch(
            "app.services.scheduler.scheduled_tasks_repo.record_task_run",
            side_effect=fake_record,
        ),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task())

    assert recorded[-1]["status"] == "succeeded"
    data = json.loads(recorded[-1]["details"])
    assert data["all_ok"] is False


@pytest.mark.asyncio
async def test_refresh_m365_consent_status_records_failure_on_exception():
    """Handler records status=failed when check_enterprise_app_permissions raises."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    with (
        patch(
            "app.services.scheduler.m365_service.check_enterprise_app_permissions",
            AsyncMock(side_effect=Exception("Graph API unavailable")),
        ),
        patch(
            "app.services.scheduler.scheduled_tasks_repo.record_task_run",
            side_effect=fake_record,
        ),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task())

    assert recorded[-1]["status"] == "failed"
    data = json.loads(recorded[-1]["details"])
    assert "Graph API unavailable" in data["error"]


@pytest.mark.asyncio
async def test_refresh_m365_consent_status_all_ok_false_when_no_results():
    """all_ok is False when check returns an empty list (nothing verified)."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    with (
        patch(
            "app.services.scheduler.m365_service.check_enterprise_app_permissions",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.services.scheduler.scheduled_tasks_repo.record_task_run",
            side_effect=fake_record,
        ),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task())

    assert recorded[-1]["status"] == "succeeded"
    data = json.loads(recorded[-1]["details"])
    assert data["all_ok"] is False
    assert data["apps_checked"] == 0


@pytest.mark.asyncio
async def test_refresh_m365_consent_status_all_companies_success():
    """Without a company_id the handler runs for every provisioned company."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    with (
        patch(
            "app.services.scheduler.m365_service.check_enterprise_app_permissions",
            new_callable=AsyncMock,
            return_value=[{"name": "Microsoft Graph", "all_ok": True}],
        ),
        patch(
            "app.services.scheduler.scheduled_tasks_repo.record_task_run",
            side_effect=fake_record,
        ),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
        patch(
            "app.services.scheduler.m365_repo.list_provisioned_company_ids",
            new_callable=AsyncMock,
            return_value={1, 2, 3},
        ),
    ):
        mock_lock.return_value.__aenter__.return_value = True
        task = {"id": 20, "command": "refresh_m365_consent_status"}
        await scheduler._run_task(task)

    assert recorded[-1]["status"] == "succeeded"
    data = json.loads(recorded[-1]["details"])
    assert data["companies_checked"] == 3
    assert len(data["results"]) == 3
    assert all(r["all_ok"] is True for r in data["results"])


@pytest.mark.asyncio
async def test_refresh_m365_consent_status_all_companies_partial_failure():
    """Status is 'failed' when at least one company raises an exception."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    call_count = 0

    async def fake_check(cid: int):
        nonlocal call_count
        call_count += 1
        if cid == 2:
            raise Exception("Graph API unavailable")
        return [{"name": "Microsoft Graph", "all_ok": True}]

    with (
        patch(
            "app.services.scheduler.m365_service.check_enterprise_app_permissions",
            side_effect=fake_check,
        ),
        patch(
            "app.services.scheduler.scheduled_tasks_repo.record_task_run",
            side_effect=fake_record,
        ),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
        patch(
            "app.services.scheduler.m365_repo.list_provisioned_company_ids",
            new_callable=AsyncMock,
            return_value={1, 2},
        ),
    ):
        mock_lock.return_value.__aenter__.return_value = True
        task = {"id": 20, "command": "refresh_m365_consent_status"}
        await scheduler._run_task(task)

    assert recorded[-1]["status"] == "failed"
    data = json.loads(recorded[-1]["details"])
    assert data["companies_checked"] == 2
    failed_entries = [r for r in data["results"] if "error" in r]
    assert len(failed_entries) == 1
    assert "Graph API unavailable" in failed_entries[0]["error"]


@pytest.mark.asyncio
async def test_refresh_m365_consent_status_skips_when_no_provisioned_companies():
    """Status is 'skipped' when no M365 companies are provisioned."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status})

    with (
        patch(
            "app.services.scheduler.m365_service.check_enterprise_app_permissions",
            new_callable=AsyncMock,
        ) as mock_check,
        patch(
            "app.services.scheduler.scheduled_tasks_repo.record_task_run",
            side_effect=fake_record,
        ),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
        patch(
            "app.services.scheduler.m365_repo.list_provisioned_company_ids",
            new_callable=AsyncMock,
            return_value=set(),
        ),
    ):
        mock_lock.return_value.__aenter__.return_value = True
        task = {"id": 20, "command": "refresh_m365_consent_status"}
        await scheduler._run_task(task)

    assert recorded[-1]["status"] == "skipped"
    mock_check.assert_not_awaited()
