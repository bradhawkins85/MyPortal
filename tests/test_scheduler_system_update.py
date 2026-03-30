from __future__ import annotations
import asyncio
from pathlib import Path

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


def test_system_update_schedules_flag_when_remote_ahead(monkeypatch, tmp_path: Path):
    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "system_update.flag"

    async def fake_get_git_ref(self, ref: str):
        assert ref == "HEAD"
        return "localsha"

    async def fake_get_remote_main_ref(self):
        return "remotesha"

    monkeypatch.setattr(SchedulerService, "_get_git_ref", fake_get_git_ref)
    monkeypatch.setattr(SchedulerService, "_get_remote_main_ref", fake_get_remote_main_ref)
    monkeypatch.setattr("app.services.scheduler._SYSTEM_UPDATE_FLAG_PATH", flag_path)

    output = asyncio.run(scheduler._run_system_update(force_restart=True))

    assert "Update scheduled" in output
    assert flag_path.exists()
    contents = flag_path.read_text(encoding="utf-8")
    assert "requested_from_ui=true" in contents
    assert "local_head=localsha" in contents
    assert "remote_head=remotesha" in contents


def test_system_update_skips_when_already_current(monkeypatch, tmp_path: Path):
    scheduler = SchedulerService()
    flag_path = tmp_path / "var" / "state" / "system_update.flag"

    async def fake_get_git_ref(self, ref: str):
        assert ref == "HEAD"
        return "same"

    async def fake_get_remote_main_ref(self):
        return "same"

    monkeypatch.setattr(SchedulerService, "_get_git_ref", fake_get_git_ref)
    monkeypatch.setattr(SchedulerService, "_get_remote_main_ref", fake_get_remote_main_ref)
    monkeypatch.setattr("app.services.scheduler._SYSTEM_UPDATE_FLAG_PATH", flag_path)

    output = asyncio.run(scheduler._run_system_update())
    assert output == "No GitHub update available; upgrade was not scheduled."
    assert not flag_path.exists()
