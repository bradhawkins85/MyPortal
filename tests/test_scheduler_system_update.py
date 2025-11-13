from __future__ import annotations
import asyncio
from types import SimpleNamespace

import pytest

from app.services.scheduler import SchedulerService
from app.repositories import scheduled_tasks as scheduled_tasks_repo


def test_run_now_forces_restart_flag(monkeypatch):
    scheduler = SchedulerService()

    async def fake_get_task(task_id: int):
        return {"id": task_id, "command": "system_update"}

    monkeypatch.setattr(scheduled_tasks_repo, "get_task", fake_get_task)

    async def fake_record_task_run(*args, **kwargs):
        return None

    monkeypatch.setattr(scheduled_tasks_repo, "record_task_run", fake_record_task_run)

    recorded: dict[str, bool] = {}

    async def fake_run_system_update(self, *, force_restart: bool = False):
        recorded["force_restart"] = force_restart
        return "ok"

    monkeypatch.setattr(SchedulerService, "run_system_update", fake_run_system_update)

    asyncio.run(scheduler.run_now(7))

    assert recorded["force_restart"] is True


def test_system_update_sets_force_restart_env(monkeypatch):
    scheduler = SchedulerService()
    captured: dict[str, dict[str, str] | None] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["env"] = kwargs.get("env")

        async def _communicate():
            return b"ok", b""

        return SimpleNamespace(returncode=0, communicate=_communicate)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    output = asyncio.run(scheduler._run_system_update(force_restart=True))

    assert captured["env"] is not None
    assert captured["env"].get("FORCE_RESTART") == "1"
    assert output == "ok"


def test_system_update_default_env_sets_force_restart_false(monkeypatch):
    scheduler = SchedulerService()
    captured: dict[str, dict[str, str] | None] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["env"] = kwargs.get("env")

        async def _communicate():
            return b"done", b""

        return SimpleNamespace(returncode=0, communicate=_communicate)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    output = asyncio.run(scheduler._run_system_update())

    assert captured["env"] is not None
    assert captured["env"].get("FORCE_RESTART") == "0"
    assert output == "done"
