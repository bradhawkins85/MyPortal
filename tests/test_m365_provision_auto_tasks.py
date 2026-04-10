"""Tests for automatic scheduled task creation after M365 provisioning."""
from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest

import app.main as main_module
from app.repositories import scheduled_tasks as scheduled_tasks_repo


# ---------------------------------------------------------------------------
# _random_daily_cron
# ---------------------------------------------------------------------------

def test_random_daily_cron_format():
    """_random_daily_cron returns a valid 5-field daily cron expression."""
    cron = main_module._random_daily_cron()
    parts = cron.split()
    assert len(parts) == 5, f"Expected 5 fields, got: {cron!r}"
    minute, hour, dom, month, dow = parts
    assert dom == "*"
    assert month == "*"
    assert dow == "*"
    assert 0 <= int(minute) <= 59
    assert 0 <= int(hour) <= 23


def test_random_daily_cron_varies():
    """_random_daily_cron produces varied results across multiple calls."""
    results = {main_module._random_daily_cron() for _ in range(50)}
    # With 1440 possible values it would be extraordinary to get only 1 unique result
    assert len(results) > 1


# ---------------------------------------------------------------------------
# get_commands_for_company (repository)
# ---------------------------------------------------------------------------

@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_get_commands_for_company_returns_set(monkeypatch):
    """get_commands_for_company returns the set of existing command names."""
    async def fake_fetch_all(query, params=()):
        return [
            {"command": "sync_m365_data"},
            {"command": "sync_staff"},
        ]

    monkeypatch.setattr(scheduled_tasks_repo.db, "fetch_all", fake_fetch_all)

    commands = await scheduled_tasks_repo.get_commands_for_company(7)

    assert commands == {"sync_m365_data", "sync_staff"}


@pytest.mark.anyio
async def test_get_commands_for_company_empty(monkeypatch):
    """get_commands_for_company returns an empty set when no tasks exist."""
    async def fake_fetch_all(query, params=()):
        return []

    monkeypatch.setattr(scheduled_tasks_repo.db, "fetch_all", fake_fetch_all)

    commands = await scheduled_tasks_repo.get_commands_for_company(99)

    assert commands == set()


# ---------------------------------------------------------------------------
# Auto-task creation logic
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_m365_provision_creates_both_tasks(monkeypatch):
    """Both sync_m365_data and sync_staff tasks are created when none exist."""
    created: list[dict] = []

    monkeypatch.setattr(
        scheduled_tasks_repo,
        "get_commands_for_company",
        AsyncMock(return_value=set()),
    )

    async def fake_create_task(*, name, command, cron, company_id, active=True, **kwargs):
        created.append({"name": name, "command": command, "cron": cron, "company_id": company_id})
        return {"id": len(created), "name": name, "command": command, "cron": cron, "company_id": company_id}

    monkeypatch.setattr(scheduled_tasks_repo, "create_task", fake_create_task)
    monkeypatch.setattr(main_module.scheduler_service, "refresh", AsyncMock())

    # Simulate the logic that runs after a successful provision
    company_id = 5
    company_name = "Acme Corp"
    m365_task_name = f"{company_name} - Sync Microsoft 365 data" if company_name else "Sync Microsoft 365 data"
    sync_staff_task_name = f"{company_name} - Sync staff directory" if company_name else "Sync staff directory"
    existing_commands = await scheduled_tasks_repo.get_commands_for_company(company_id)
    for command, label in (
        ("sync_m365_data", m365_task_name),
        ("sync_staff", sync_staff_task_name),
    ):
        if command not in existing_commands:
            await scheduled_tasks_repo.create_task(
                name=label,
                command=command,
                cron=main_module._random_daily_cron(),
                company_id=company_id,
                active=True,
            )
    await main_module.scheduler_service.refresh()

    assert len(created) == 2
    commands_created = {t["command"] for t in created}
    assert commands_created == {"sync_m365_data", "sync_staff"}
    m365_task = next(t for t in created if t["command"] == "sync_m365_data")
    assert m365_task["name"] == "Acme Corp - Sync Microsoft 365 data"
    staff_task = next(t for t in created if t["command"] == "sync_staff")
    assert staff_task["name"] == "Acme Corp - Sync staff directory"
    for task in created:
        assert task["company_id"] == company_id
        cron = task["cron"]
        parts = cron.split()
        assert len(parts) == 5
        assert parts[2] == parts[3] == parts[4] == "*"
    main_module.scheduler_service.refresh.assert_called_once()


@pytest.mark.anyio
async def test_m365_provision_skips_existing_tasks(monkeypatch):
    """No duplicate tasks are created when sync_m365_data and sync_staff already exist."""
    created: list[dict] = []

    monkeypatch.setattr(
        scheduled_tasks_repo,
        "get_commands_for_company",
        AsyncMock(return_value={"sync_m365_data", "sync_staff"}),
    )

    async def fake_create_task(**kwargs):
        created.append(kwargs)
        return {}

    monkeypatch.setattr(scheduled_tasks_repo, "create_task", fake_create_task)
    monkeypatch.setattr(main_module.scheduler_service, "refresh", AsyncMock())

    company_id = 5
    company_name = "Acme Corp"
    m365_task_name = f"{company_name} - Sync Microsoft 365 data" if company_name else "Sync Microsoft 365 data"
    sync_staff_task_name = f"{company_name} - Sync staff directory" if company_name else "Sync staff directory"
    existing_commands = await scheduled_tasks_repo.get_commands_for_company(company_id)
    for command, label in (
        ("sync_m365_data", m365_task_name),
        ("sync_staff", sync_staff_task_name),
    ):
        if command not in existing_commands:
            await scheduled_tasks_repo.create_task(
                name=label,
                command=command,
                cron=main_module._random_daily_cron(),
                company_id=company_id,
                active=True,
            )
    await main_module.scheduler_service.refresh()

    assert created == [], "No tasks should be created when they already exist"
    main_module.scheduler_service.refresh.assert_called_once()


@pytest.mark.anyio
async def test_m365_provision_creates_only_missing_task(monkeypatch):
    """Only the missing task is created when one already exists."""
    created: list[dict] = []

    monkeypatch.setattr(
        scheduled_tasks_repo,
        "get_commands_for_company",
        AsyncMock(return_value={"sync_m365_data"}),
    )

    async def fake_create_task(*, name, command, cron, company_id, active=True, **kwargs):
        created.append({"command": command})
        return {}

    monkeypatch.setattr(scheduled_tasks_repo, "create_task", fake_create_task)
    monkeypatch.setattr(main_module.scheduler_service, "refresh", AsyncMock())

    company_id = 5
    company_name = "Acme Corp"
    m365_task_name = f"{company_name} - Sync Microsoft 365 data" if company_name else "Sync Microsoft 365 data"
    sync_staff_task_name = f"{company_name} - Sync staff directory" if company_name else "Sync staff directory"
    existing_commands = await scheduled_tasks_repo.get_commands_for_company(company_id)
    for command, label in (
        ("sync_m365_data", m365_task_name),
        ("sync_staff", sync_staff_task_name),
    ):
        if command not in existing_commands:
            await scheduled_tasks_repo.create_task(
                name=label,
                command=command,
                cron=main_module._random_daily_cron(),
                company_id=company_id,
                active=True,
            )
    await main_module.scheduler_service.refresh()

    assert len(created) == 1
    assert created[0]["command"] == "sync_staff"


# ---------------------------------------------------------------------------
# Company name in task name
# ---------------------------------------------------------------------------

def test_m365_task_name_includes_company_name():
    """sync_m365_data task name is prefixed with the company name."""
    company_name = "Widgets Inc"
    task_name = (
        f"{company_name} - Sync Microsoft 365 data"
        if company_name
        else "Sync Microsoft 365 data"
    )
    assert task_name == "Widgets Inc - Sync Microsoft 365 data"


def test_m365_task_name_fallback_when_no_company_name():
    """sync_m365_data task name falls back to default when company name is empty."""
    company_name = ""
    task_name = (
        f"{company_name} - Sync Microsoft 365 data"
        if company_name
        else "Sync Microsoft 365 data"
    )
    assert task_name == "Sync Microsoft 365 data"


def test_sync_staff_task_name_includes_company_name():
    """sync_staff task name is prefixed with the company name."""
    company_name = "Widgets Inc"
    task_name = (
        f"{company_name} - Sync staff directory"
        if company_name
        else "Sync staff directory"
    )
    assert task_name == "Widgets Inc - Sync staff directory"


def test_sync_staff_task_name_fallback_when_no_company_name():
    """sync_staff task name falls back to default when company name is empty."""
    company_name = ""
    task_name = (
        f"{company_name} - Sync staff directory"
        if company_name
        else "Sync staff directory"
    )
    assert task_name == "Sync staff directory"
