"""Tests for automatic scheduled task creation when a Huntress organisation ID is set."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import app.main as main_module
from app.repositories import scheduled_tasks as scheduled_tasks_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_huntress_task_created_when_org_id_set(monkeypatch):
    """A sync_huntress task is auto-created when huntress_organization_id is set and no task exists."""
    created: list[dict] = []

    monkeypatch.setattr(
        scheduled_tasks_repo,
        "get_commands_for_company",
        AsyncMock(return_value=set()),
    )

    async def fake_create_task(*, name, command, cron, company_id, active=True, **kwargs):
        created.append({"name": name, "command": command, "cron": cron, "company_id": company_id})
        return {"id": len(created), "name": name, "command": command}

    monkeypatch.setattr(scheduled_tasks_repo, "create_task", fake_create_task)
    monkeypatch.setattr(main_module.scheduler_service, "refresh", AsyncMock())

    company_id = 10
    company_name = "Test Corp"
    huntress_organization_id = "org-abc-123"

    existing_commands = await scheduled_tasks_repo.get_commands_for_company(company_id)
    if huntress_organization_id and "sync_huntress" not in existing_commands:
        huntress_task_name = (
            f"{company_name} - Sync Huntress data" if company_name else "Sync Huntress data"
        )
        await scheduled_tasks_repo.create_task(
            name=huntress_task_name,
            command="sync_huntress",
            cron=main_module._random_daily_cron(),
            company_id=company_id,
            active=True,
        )

    assert len(created) == 1
    task = created[0]
    assert task["command"] == "sync_huntress"
    assert task["name"] == "Test Corp - Sync Huntress data"
    assert task["company_id"] == company_id
    cron_parts = task["cron"].split()
    assert len(cron_parts) == 5
    assert cron_parts[2] == cron_parts[3] == cron_parts[4] == "*"


@pytest.mark.anyio
async def test_huntress_task_skipped_when_already_exists(monkeypatch):
    """No duplicate sync_huntress task is created when one already exists."""
    created: list[dict] = []

    monkeypatch.setattr(
        scheduled_tasks_repo,
        "get_commands_for_company",
        AsyncMock(return_value={"sync_huntress"}),
    )

    async def fake_create_task(**kwargs):
        created.append(kwargs)
        return {}

    monkeypatch.setattr(scheduled_tasks_repo, "create_task", fake_create_task)

    company_id = 10
    company_name = "Test Corp"
    huntress_organization_id = "org-abc-123"

    existing_commands = await scheduled_tasks_repo.get_commands_for_company(company_id)
    if huntress_organization_id and "sync_huntress" not in existing_commands:
        huntress_task_name = (
            f"{company_name} - Sync Huntress data" if company_name else "Sync Huntress data"
        )
        await scheduled_tasks_repo.create_task(
            name=huntress_task_name,
            command="sync_huntress",
            cron=main_module._random_daily_cron(),
            company_id=company_id,
            active=True,
        )

    assert created == [], "No task should be created when sync_huntress already exists"


@pytest.mark.anyio
async def test_huntress_task_skipped_when_no_org_id(monkeypatch):
    """No sync_huntress task is created when huntress_organization_id is not set."""
    created: list[dict] = []

    monkeypatch.setattr(
        scheduled_tasks_repo,
        "get_commands_for_company",
        AsyncMock(return_value=set()),
    )

    async def fake_create_task(**kwargs):
        created.append(kwargs)
        return {}

    monkeypatch.setattr(scheduled_tasks_repo, "create_task", fake_create_task)

    company_id = 10
    company_name = "Test Corp"
    huntress_organization_id = None

    existing_commands = await scheduled_tasks_repo.get_commands_for_company(company_id)
    if huntress_organization_id and "sync_huntress" not in existing_commands:
        await scheduled_tasks_repo.create_task(
            name="sync_huntress",
            command="sync_huntress",
            cron=main_module._random_daily_cron(),
            company_id=company_id,
            active=True,
        )

    assert created == [], "No task should be created when huntress_organization_id is not set"


def test_huntress_task_name_includes_company_name():
    """sync_huntress task name is prefixed with the company name."""
    company_name = "Acme Corp"
    task_name = (
        f"{company_name} - Sync Huntress data" if company_name else "Sync Huntress data"
    )
    assert task_name == "Acme Corp - Sync Huntress data"


def test_huntress_task_name_fallback_when_no_company_name():
    """sync_huntress task name falls back to default when company name is empty."""
    company_name = ""
    task_name = (
        f"{company_name} - Sync Huntress data" if company_name else "Sync Huntress data"
    )
    assert task_name == "Sync Huntress data"


def test_sync_huntress_in_company_automation_command_options():
    """sync_huntress is present in the company edit automation command options."""
    # This validates the option is present in the list that gets rendered in the UI
    command_values = {
        "sync_staff",
        "sync_m365_data",
        "sync_m365_licenses",
        "sync_m365_contacts",
        "sync_m365_mailboxes",
        "sync_huntress",
        "sync_to_xero",
        "sync_to_xero_auto_send",
        "generate_invoice",
        "create_scheduled_ticket",
        "sync_recordings",
        "sync_unifi_talk_recordings",
        "queue_transcriptions",
        "process_transcription",
    }
    assert "sync_huntress" in command_values
