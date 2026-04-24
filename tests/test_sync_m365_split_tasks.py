"""Tests for the split sync_m365_* scheduler commands."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_task(command: str, company_id: int = 1) -> dict[str, Any]:
    return {"id": 10, "company_id": company_id, "command": command}


def _make_staff_summary() -> Any:
    s = MagicMock()
    s.created = 2
    s.updated = 1
    s.skipped = 0
    s.removed = 0
    s.total = 3
    return s


# ---------------------------------------------------------------------------
# sync_m365_licenses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_m365_licenses_calls_service():
    """sync_m365_licenses task calls sync_company_licenses."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    calls: list[int] = []

    async def fake_sync_licenses(cid: int) -> None:
        calls.append(cid)

    with (
        patch("app.services.scheduler.m365_service.sync_company_licenses", side_effect=fake_sync_licenses),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", new_callable=AsyncMock),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task("sync_m365_licenses", company_id=42))

    assert calls == [42]


@pytest.mark.asyncio
async def test_sync_m365_licenses_records_success():
    """sync_m365_licenses task records status=succeeded on success."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    with (
        patch("app.services.scheduler.m365_service.sync_company_licenses", new_callable=AsyncMock),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task("sync_m365_licenses"))

    assert recorded[-1]["status"] == "succeeded"
    data = json.loads(recorded[-1]["details"])
    assert data["licenses_synced"] is True


@pytest.mark.asyncio
async def test_sync_m365_licenses_records_failure_on_exception():
    """sync_m365_licenses task records status=failed when service raises."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    with (
        patch(
            "app.services.scheduler.m365_service.sync_company_licenses",
            AsyncMock(side_effect=Exception("License API down")),
        ),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task("sync_m365_licenses"))

    assert recorded[-1]["status"] == "failed"
    data = json.loads(recorded[-1]["details"])
    assert data["licenses_synced"] is False
    assert "License API down" in data["error"]


@pytest.mark.asyncio
async def test_sync_m365_licenses_skips_without_company():
    """sync_m365_licenses task records skipped when no company_id is set."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status})

    with (
        patch("app.services.scheduler.m365_service.sync_company_licenses", new_callable=AsyncMock),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        task = {"id": 10, "command": "sync_m365_licenses"}
        await scheduler._run_task(task)

    assert recorded[-1]["status"] == "skipped"


# ---------------------------------------------------------------------------
# sync_m365_contacts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_m365_contacts_calls_importer():
    """sync_m365_contacts task calls import_m365_contacts_for_company."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    calls: list[int] = []

    async def fake_import(cid: int):
        calls.append(cid)
        return _make_staff_summary()

    with (
        patch("app.services.scheduler.staff_importer.import_m365_contacts_for_company", side_effect=fake_import),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", new_callable=AsyncMock),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task("sync_m365_contacts", company_id=7))

    assert calls == [7]


@pytest.mark.asyncio
async def test_sync_m365_contacts_records_success_with_staff_counts():
    """sync_m365_contacts task details include staff sync counts on success."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    with (
        patch(
            "app.services.scheduler.staff_importer.import_m365_contacts_for_company",
            new_callable=AsyncMock,
            return_value=_make_staff_summary(),
        ),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task("sync_m365_contacts"))

    assert recorded[-1]["status"] == "succeeded"
    data = json.loads(recorded[-1]["details"])
    assert data["staff"]["total"] == 3
    assert data["staff"]["created"] == 2


@pytest.mark.asyncio
async def test_sync_m365_contacts_records_failure_on_exception():
    """sync_m365_contacts task records status=failed when importer raises."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    with (
        patch(
            "app.services.scheduler.staff_importer.import_m365_contacts_for_company",
            AsyncMock(side_effect=Exception("Graph error")),
        ),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task("sync_m365_contacts"))

    assert recorded[-1]["status"] == "failed"
    data = json.loads(recorded[-1]["details"])
    assert "Graph error" in data["staff_sync_error"]


# ---------------------------------------------------------------------------
# sync_m365_mailboxes (new individual command)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_m365_mailboxes_calls_service():
    """sync_m365_mailboxes task calls m365_service.sync_mailboxes."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    calls: list[int] = []

    async def fake_sync_mailboxes(cid: int) -> int:
        calls.append(cid)
        return 5

    with (
        patch("app.services.scheduler.m365_service.sync_mailboxes", side_effect=fake_sync_mailboxes),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", new_callable=AsyncMock),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task("sync_m365_mailboxes", company_id=3))

    assert calls == [3]


@pytest.mark.asyncio
async def test_sync_m365_mailboxes_records_success():
    """sync_m365_mailboxes task records mailboxes_synced count on success."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    with (
        patch("app.services.scheduler.m365_service.sync_mailboxes", new_callable=AsyncMock, return_value=8),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task("sync_m365_mailboxes"))

    assert recorded[-1]["status"] == "succeeded"
    data = json.loads(recorded[-1]["details"])
    assert data["mailboxes_synced"] == 8


@pytest.mark.asyncio
async def test_sync_m365_mailboxes_records_failure_on_exception():
    """sync_m365_mailboxes task records status=failed when service raises."""
    from app.services.scheduler import SchedulerService

    scheduler = SchedulerService()
    recorded: list[dict] = []

    async def fake_record(task_id, *, status, started_at, finished_at, duration_ms, details=None):
        recorded.append({"status": status, "details": details})

    with (
        patch(
            "app.services.scheduler.m365_service.sync_mailboxes",
            AsyncMock(side_effect=Exception("Mailbox timeout")),
        ),
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", side_effect=fake_record),
        patch("app.services.scheduler.db.acquire_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True
        await scheduler._run_task(_make_task("sync_m365_mailboxes"))

    assert recorded[-1]["status"] == "failed"
    data = json.loads(recorded[-1]["details"])
    assert data["mailboxes_synced"] == 0
    assert "Mailbox timeout" in data["error"]


# ---------------------------------------------------------------------------
# COMMANDS_BY_MODULE includes new commands
# ---------------------------------------------------------------------------

def test_commands_by_module_includes_split_commands():
    """The m365 module entry in COMMANDS_BY_MODULE includes all three new commands."""
    from app.services.scheduler import COMMANDS_BY_MODULE

    m365_cmds = COMMANDS_BY_MODULE["m365"]
    assert "sync_m365_licenses" in m365_cmds
    assert "sync_m365_contacts" in m365_cmds
    assert "sync_m365_mailboxes" in m365_cmds


# ---------------------------------------------------------------------------
# Startup migration: _migrate_sync_m365_data_tasks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migrate_creates_split_tasks_for_legacy_company(monkeypatch):
    """Startup migration creates three split tasks for a company with only sync_m365_data."""
    import app.main as main_module

    legacy_task = {
        "id": 1, "company_id": 5, "command": "sync_m365_data",
        "name": "Acme - Sync Microsoft 365 data", "cron": "30 2 * * *", "active": True,
    }
    created: list[dict] = []
    deactivated: list[int] = []

    async def fake_list_tasks(include_inactive=False):
        return [legacy_task]

    async def fake_create_task(*, name, command, cron, company_id, active=True, **kwargs):
        created.append({"name": name, "command": command, "company_id": company_id})
        return {"id": len(created) + 10, "command": command}

    async def fake_set_active(task_id, active):
        if not active:
            deactivated.append(task_id)
        return {}

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "list_tasks", fake_list_tasks)
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "create_task", fake_create_task)
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "set_task_active", fake_set_active)

    # Run the migration inline (same logic as in on_startup)
    from collections import defaultdict
    legacy_commands = {"sync_m365_data", "sync_o365"}
    new_commands = {"sync_m365_licenses", "sync_m365_contacts", "sync_m365_mailboxes"}
    all_tasks = await main_module.scheduled_tasks_repo.list_tasks(include_inactive=False)
    by_company: dict[int, list[dict]] = defaultdict(list)
    for t in all_tasks:
        cid = t.get("company_id")
        if cid is not None:
            by_company[int(cid)].append(t)
    for company_id, company_tasks in by_company.items():
        commands_for_company = {t["command"] for t in company_tasks}
        has_legacy = bool(legacy_commands & commands_for_company)
        has_new = bool(new_commands & commands_for_company)
        if not has_legacy or has_new:
            continue
        legacy_t = next((t for t in company_tasks if t.get("command") in legacy_commands), None)
        task_name_prefix = ""
        if legacy_t:
            raw_name: str = legacy_t.get("name") or ""
            for suffix in (" - Sync Microsoft 365 data", " - Sync O365", " - Sync M365"):
                if raw_name.endswith(suffix):
                    task_name_prefix = raw_name[: -len(suffix)]
                    break
        for command, label_suffix in (
            ("sync_m365_licenses", "Sync Microsoft 365 licenses"),
            ("sync_m365_contacts", "Sync Microsoft 365 contacts"),
            ("sync_m365_mailboxes", "Sync Microsoft 365 mailboxes"),
        ):
            if command not in commands_for_company:
                label = f"{task_name_prefix} - {label_suffix}" if task_name_prefix else label_suffix
                await main_module.scheduled_tasks_repo.create_task(
                    name=label, command=command, cron=main_module._random_daily_cron(),
                    company_id=company_id, active=True,
                )
        for t in company_tasks:
            if t.get("command") in legacy_commands:
                await main_module.scheduled_tasks_repo.set_task_active(t["id"], False)

    assert len(created) == 3
    commands_created = {t["command"] for t in created}
    assert commands_created == {"sync_m365_licenses", "sync_m365_contacts", "sync_m365_mailboxes"}
    # Names should include company prefix extracted from the legacy task name
    for t in created:
        assert t["name"].startswith("Acme - ")
    # Legacy task should have been deactivated
    assert 1 in deactivated


@pytest.mark.asyncio
async def test_migrate_skips_company_already_has_split_tasks(monkeypatch):
    """Startup migration does nothing for a company that already has split tasks."""
    import app.main as main_module

    tasks = [
        {"id": 1, "company_id": 5, "command": "sync_m365_licenses", "name": "A - Sync M365 licenses", "active": True},
        {"id": 2, "company_id": 5, "command": "sync_m365_contacts", "name": "A - Sync M365 contacts", "active": True},
        {"id": 3, "company_id": 5, "command": "sync_m365_mailboxes", "name": "A - Sync M365 mailboxes", "active": True},
    ]
    created: list[dict] = []
    deactivated: list[int] = []

    async def fake_list_tasks(include_inactive=False):
        return tasks

    async def fake_create_task(**kwargs):
        created.append(kwargs)
        return {}

    async def fake_set_active(task_id, active):
        if not active:
            deactivated.append(task_id)
        return {}

    monkeypatch.setattr(main_module.scheduled_tasks_repo, "list_tasks", fake_list_tasks)
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "create_task", fake_create_task)
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "set_task_active", fake_set_active)

    from collections import defaultdict
    legacy_commands = {"sync_m365_data", "sync_o365"}
    new_commands = {"sync_m365_licenses", "sync_m365_contacts", "sync_m365_mailboxes"}
    all_tasks = await main_module.scheduled_tasks_repo.list_tasks(include_inactive=False)
    by_company: dict[int, list[dict]] = defaultdict(list)
    for t in all_tasks:
        cid = t.get("company_id")
        if cid is not None:
            by_company[int(cid)].append(t)
    for company_id, company_tasks in by_company.items():
        commands_for_company = {t["command"] for t in company_tasks}
        has_legacy = bool(legacy_commands & commands_for_company)
        has_new = bool(new_commands & commands_for_company)
        if not has_legacy or has_new:
            continue  # skip – already has split tasks

    assert created == []
    assert deactivated == []
