"""Tests for automatic scheduling of the M365 contact, mailbox and licence syncs."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services import m365 as m365_service
from app.services import m365_sync_schedule as svc

COMPLETE = {"tenant_id": "t", "client_id": "c", "client_secret": "s"}
ALL_COMMANDS = {"sync_m365_contacts", "sync_m365_mailboxes", "sync_m365_licenses"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def repos(monkeypatch):
    state = {
        "credentials": {1: COMPLETE},
        "active": {1: {"id": 5}},
        "pending": {},
        "commands": {},
        "created": [],
    }

    async def get_credentials(cid):
        return state["credentials"].get(cid)

    async def get_active(cid):
        return state["active"].get(cid)

    async def get_pending(cid):
        return state["pending"].get(cid)

    async def get_commands(cid):
        return set(state["commands"].get(cid, set()))

    async def create_task(**kwargs):
        state["created"].append(kwargs)
        state["commands"].setdefault(kwargs["company_id"], set()).add(kwargs["command"])
        return kwargs

    async def get_company(cid):
        return {"id": cid, "name": "Acme"}

    async def provisioned():
        return set(state["credentials"])

    monkeypatch.setattr(svc.m365_repo, "get_credentials", get_credentials)
    monkeypatch.setattr(svc.m365_repo, "list_provisioned_company_ids", provisioned)
    monkeypatch.setattr(svc.connection_repo, "get_active", get_active)
    monkeypatch.setattr(svc.connection_repo, "get_pending", get_pending)
    monkeypatch.setattr(svc.scheduled_tasks_repo, "get_commands_for_company", get_commands)
    monkeypatch.setattr(svc.scheduled_tasks_repo, "create_task", create_task)
    monkeypatch.setattr(svc.company_repo, "get_company_by_id", get_company)
    return state


def test_random_daily_cron_is_daily_and_in_range():
    for _ in range(200):
        minute, hour, dom, month, dow = svc.random_daily_cron().split()
        assert (dom, month, dow) == ("*", "*", "*")
        assert 0 <= int(minute) <= 59 and 0 <= int(hour) <= 23


@pytest.mark.anyio
async def test_connected_tenant_gets_all_three_tasks_at_random_times(repos):
    created = await svc.ensure_sync_tasks(1)

    assert set(created) == ALL_COMMANDS
    names = {t["name"] for t in repos["created"]}
    assert "Acme - Sync Microsoft 365 contacts" in names
    for task in repos["created"]:
        assert task["company_id"] == 1 and task["active"] is True
        assert task["cron"].endswith(" * * *")


@pytest.mark.anyio
async def test_existing_tasks_are_not_duplicated(repos):
    repos["commands"][1] = {"sync_m365_contacts"}

    created = await svc.ensure_sync_tasks(1)

    assert set(created) == {"sync_m365_mailboxes", "sync_m365_licenses"}
    assert await svc.ensure_sync_tasks(1) == []


@pytest.mark.anyio
async def test_unconnected_tenants_are_skipped(repos):
    repos["credentials"][2] = {"tenant_id": "t", "client_id": "", "client_secret": ""}
    repos["credentials"][3] = COMPLETE
    repos["pending"][3] = {"id": 9}  # first setup staged, not yet verified

    assert await svc.ensure_sync_tasks(2) == []
    assert await svc.ensure_sync_tasks(3) == []
    assert await svc.ensure_sync_tasks(4) == []
    assert repos["created"] == []


@pytest.mark.anyio
async def test_legacy_connection_without_connection_rows_counts(repos):
    repos["credentials"][6] = COMPLETE

    assert set(await svc.ensure_sync_tasks(6)) == ALL_COMMANDS


@pytest.mark.anyio
async def test_backfill_adds_missing_tasks_for_connected_tenants(repos):
    repos["credentials"][2] = COMPLETE  # legacy, no tasks
    repos["credentials"][3] = COMPLETE
    repos["commands"][3] = set(ALL_COMMANDS)

    result = await svc.backfill_sync_tasks()

    assert result == {"companies_updated": 2, "tasks_created": 6}
    assert {t["company_id"] for t in repos["created"]} == {1, 2}


@pytest.mark.anyio
async def test_ensure_and_refresh_swallows_errors(monkeypatch):
    monkeypatch.setattr(svc, "ensure_sync_tasks", AsyncMock(side_effect=RuntimeError("db down")))

    assert await svc.ensure_sync_tasks_and_refresh(1) == []


@pytest.mark.anyio
async def test_activating_connection_schedules_sync_tasks(monkeypatch):
    monkeypatch.setattr(
        m365_service.connection_repo, "activate", AsyncMock(return_value={"id": 5})
    )
    ensure = AsyncMock(return_value=[])
    monkeypatch.setattr(svc, "ensure_sync_tasks_and_refresh", ensure)

    assert await m365_service.activate_connection_candidate(1, 5) == {"id": 5}
    ensure.assert_awaited_once_with(1)


def test_new_task_modal_defaults_to_random_time_not_2am():
    js = Path("app/static/js/scheduled_tasks.js").read_text()
    assert "|| '0 2 * * *'" not in js
    assert "randomDailyCron()" in js
