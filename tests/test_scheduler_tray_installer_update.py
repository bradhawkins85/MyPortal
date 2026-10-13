from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.scheduler import SchedulerService


@pytest.fixture(autouse=True)
def enabled_modules(monkeypatch):
    monkeypatch.setattr("app.services.scheduler.module_repo.get_module", AsyncMock(return_value={"enabled": True}))
    monkeypatch.setattr("app.services.scheduler.scheduled_tasks_repo.has_run_since", AsyncMock(return_value=False))


def test_update_tray_icon_installer_fetches_latest_installers(monkeypatch):
    task = {"id": 101, "command": "update_tray_icon_installer"}
    scheduler = SchedulerService()
    fetched: dict[str, str | None] = {}

    async def fake_fetch_latest_tray_installers(
        *, repo: str, github_token: str | None = None, force: bool = False
    ):
        fetched["repo"] = repo
        fetched["github_token"] = github_token
        fetched["force"] = str(force)
        return {
            "myportal-tray.msi": True,
            "myportal-tray.dmg": True,
            "myportal-tray.pkg": False,
        }

    monkeypatch.setattr(
        "app.services.scheduler.tray_installer_service.fetch_latest_tray_installers",
        fake_fetch_latest_tray_installers,
    )

    async def fake_fetch_latest_agent(*, repo: str, github_token: str | None = None, force: bool = False):
        fetched["agent_repo"] = repo
        return {"myportal-rmm-windows-amd64.exe": False}

    monkeypatch.setattr("app.services.rmm_agent_deploy.fetch_latest_agent", fake_fetch_latest_agent)

    with (
        patch(
            "app.services.scheduler.scheduled_tasks_repo.record_task_run",
            new_callable=AsyncMock,
        ) as record_task_run,
        patch(
            "app.services.scheduler.db.acquire_lock",
        ) as mock_lock,
    ):
        mock_lock.return_value.__aenter__.return_value = True

        asyncio.run(scheduler._run_task(task))

    assert fetched["repo"] == "bradhawkins85/MyPortal"
    assert fetched["force"] == "False"
    assert fetched["agent_repo"] == "bradhawkins85/MyPortal"
    details = json.loads(record_task_run.await_args.kwargs["details"])
    assert details == {
        "repo": "bradhawkins85/MyPortal",
        "assets": {
            "myportal-tray.msi": True,
            "myportal-tray.dmg": True,
            "myportal-tray.pkg": False,
        },
        "rmm_agent": {"myportal-rmm-windows-amd64.exe": False},
        "updated": True,
    }


def test_tray_installer_failure_still_refreshes_rmm_builds(monkeypatch):
    tray_fetch = AsyncMock(side_effect=RuntimeError("tray download failed"))
    agent_fetch = AsyncMock(return_value={"myportal-rmm-windows-amd64.exe": True})
    monkeypatch.setattr("app.services.scheduler.tray_installer_service.fetch_latest_tray_installers", tray_fetch)
    monkeypatch.setattr("app.services.rmm_agent_deploy.fetch_latest_agent", agent_fetch)
    with (
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", new_callable=AsyncMock) as record,
        patch("app.services.scheduler.db.acquire_lock") as lock,
    ):
        lock.return_value.__aenter__.return_value = True
        asyncio.run(SchedulerService()._run_task({"id": 101, "command": "update_tray_icon_installer"}))
    agent_fetch.assert_awaited_once_with(repo="bradhawkins85/MyPortal", github_token=tray_fetch.await_args.kwargs["github_token"])
    assert record.await_args.kwargs["status"] == "failed"
    details = json.loads(record.await_args.kwargs["details"])
    assert details["rmm_agent"] == {"myportal-rmm-windows-amd64.exe": True}
    assert details["updated"] is True
    assert details["errors"] == {"assets": "tray download failed"}


def test_rmm_refresh_failure_is_reported_without_losing_tray_results(monkeypatch):
    monkeypatch.setattr("app.services.scheduler.tray_installer_service.fetch_latest_tray_installers", AsyncMock(return_value={"myportal-tray.msi": True}))
    monkeypatch.setattr("app.services.rmm_agent_deploy.fetch_latest_agent", AsyncMock(side_effect=RuntimeError("RMM download failed")))
    with (
        patch("app.services.scheduler.scheduled_tasks_repo.record_task_run", new_callable=AsyncMock) as record,
        patch("app.services.scheduler.db.acquire_lock") as lock,
    ):
        lock.return_value.__aenter__.return_value = True
        asyncio.run(SchedulerService()._run_task({"id": 101, "command": "update_tray_icon_installer"}))
    assert record.await_args.kwargs["status"] == "failed"
    details = json.loads(record.await_args.kwargs["details"])
    assert details["assets"] == {"myportal-tray.msi": True}
    assert details["errors"] == {"rmm_agent": "RMM download failed"}
