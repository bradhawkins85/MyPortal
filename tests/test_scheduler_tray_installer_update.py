from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.services.scheduler import SchedulerService


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
    details = json.loads(record_task_run.await_args.kwargs["details"])
    assert details == {
        "repo": "bradhawkins85/MyPortal",
        "assets": {
            "myportal-tray.msi": True,
            "myportal-tray.dmg": True,
            "myportal-tray.pkg": False,
        },
        "updated": True,
    }
