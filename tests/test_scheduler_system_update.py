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

    monkeypatch.setattr(SchedulerService, "_run_system_update", fake_run_system_update)

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


def test_system_update_default_env_has_no_force(monkeypatch):
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
    assert "FORCE_RESTART" not in captured["env"]
    assert output == "done"


def test_automation_runner_respects_configured_interval(monkeypatch):
    recorded_jobs: dict[str, dict[str, object]] = {}

    class FakeScheduler:
        def __init__(self, *, timezone=None):  # type: ignore[no-untyped-def]
            self.timezone = timezone
            self.jobs: dict[str, dict[str, object]] = {}

        def start(self) -> None:  # pragma: no cover - behaviour not needed here
            return

        def shutdown(self, wait: bool = False) -> None:  # pragma: no cover
            return

        def get_jobs(self):  # type: ignore[no-untyped-def]
            return []

        def get_job(self, job_id):  # type: ignore[no-untyped-def]
            return self.jobs.get(job_id)

        def add_job(self, func, trigger, *args, **kwargs):  # type: ignore[no-untyped-def]
            job_id = kwargs.get("id")
            if job_id:
                self.jobs[job_id] = {
                    "func": func,
                    "trigger": trigger,
                    "args": args,
                    "kwargs": kwargs,
                }
                recorded_jobs[job_id] = self.jobs[job_id]

    settings = SimpleNamespace(
        default_timezone="UTC",
        automation_runner_interval_seconds=12,
    )

    monkeypatch.setattr("app.services.scheduler.AsyncIOScheduler", FakeScheduler)
    monkeypatch.setattr("app.services.scheduler.get_settings", lambda: settings)

    scheduler = SchedulerService()
    scheduler._started = True
    scheduler._ensure_monitoring_jobs()

    automation_job = recorded_jobs.get("automation-runner")
    assert automation_job is not None
    assert automation_job["trigger"] == "interval"
    assert automation_job["kwargs"].get("seconds") == 12
