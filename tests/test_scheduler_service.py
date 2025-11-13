from __future__ import annotations

import asyncio
from datetime import timezone
from types import SimpleNamespace

import pytest

from app.services import scheduler as scheduler_module
from app.services.scheduler import SchedulerService


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_start_runs_refresh_in_background(monkeypatch):
    service = SchedulerService()

    scheduler_started = False

    def fake_start() -> None:
        nonlocal scheduler_started
        scheduler_started = True

    shutdown_called = False

    def fake_shutdown(*_, **__) -> None:
        nonlocal shutdown_called
        shutdown_called = True

    service._scheduler = SimpleNamespace(
        start=fake_start,
        shutdown=fake_shutdown,
        timezone=timezone.utc,
    )
    monkeypatch.setattr(service, "_ensure_monitoring_jobs", lambda: None)

    refresh_started = asyncio.Event()
    refresh_continue = asyncio.Event()

    async def fake_refresh(self) -> None:  # type: ignore[override]
        refresh_started.set()
        await refresh_continue.wait()

    monkeypatch.setattr(SchedulerService, "refresh", fake_refresh)

    await service.start()

    assert scheduler_started is True
    refresh_task = service._refresh_task
    assert refresh_task is not None
    assert not refresh_task.done()
    # Scheduler.start should not await refresh completion
    assert service._refresh_task is refresh_task

    await refresh_started.wait()

    refresh_continue.set()
    await refresh_task

    await service.stop()
    assert shutdown_called is True


@pytest.mark.anyio
async def test_stop_waits_for_refresh_completion(monkeypatch):
    service = SchedulerService()

    shutdown_called = False

    def fake_shutdown(*_, **__) -> None:
        nonlocal shutdown_called
        shutdown_called = True

    service._scheduler = SimpleNamespace(
        start=lambda: None,
        shutdown=fake_shutdown,
        timezone=timezone.utc,
    )
    monkeypatch.setattr(service, "_ensure_monitoring_jobs", lambda: None)

    refresh_started = asyncio.Event()
    refresh_continue = asyncio.Event()

    async def fake_refresh(self) -> None:  # type: ignore[override]
        refresh_started.set()
        await refresh_continue.wait()

    monkeypatch.setattr(SchedulerService, "refresh", fake_refresh)

    await service.start()
    assert service._refresh_task is not None
    await refresh_started.wait()

    stop_task = asyncio.create_task(service.stop())
    await asyncio.sleep(0)

    assert not stop_task.done()
    assert shutdown_called is False

    refresh_continue.set()
    await stop_task

    assert shutdown_called is True
    assert service._refresh_task is None


@pytest.mark.anyio
async def test_refresh_failure_logged(monkeypatch):
    service = SchedulerService()

    service._scheduler = SimpleNamespace(
        start=lambda: None,
        shutdown=lambda *_, **__: None,
        timezone=timezone.utc,
    )
    monkeypatch.setattr(service, "_ensure_monitoring_jobs", lambda: None)

    async def failing_refresh(self) -> None:  # type: ignore[override]
        raise RuntimeError("boom")

    monkeypatch.setattr(SchedulerService, "refresh", failing_refresh)

    logged: list[tuple[str, dict[str, object]]] = []

    def fake_log_error(message: str, **meta) -> None:
        logged.append((message, meta))

    monkeypatch.setattr(scheduler_module, "log_error", fake_log_error)

    await service.start()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert logged, "Expected scheduler refresh failure to be logged"
    assert logged[0][0] == "Scheduler refresh failed"
    assert "boom" in (logged[0][1].get("error") or "")

    await service.stop()

