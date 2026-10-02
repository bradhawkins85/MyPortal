"""Scheduled jobs run only on the blue/green slot nginx routes to."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import deployment_slot
from app.services import scheduler as scheduler_module

LIVE_BLUE = "server 127.0.0.1:8001 max_fails=1 fail_timeout=5s;\nserver 127.0.0.1:8002 down;\n"
LIVE_GREEN = "server 127.0.0.1:8002 max_fails=1 fail_timeout=5s;\nserver 127.0.0.1:8001 down;\n"


@pytest.fixture
def slot(monkeypatch, tmp_path):
    upstream = tmp_path / "myportal-active.inc"
    monkeypatch.setenv("MYPORTAL_NGINX_UPSTREAM_FILE", str(upstream))
    state = {"instance": "blue"}
    monkeypatch.setattr(
        deployment_slot, "get_settings", lambda: SimpleNamespace(app_instance_id=state["instance"]),
    )
    deployment_slot.reset_cache()
    yield SimpleNamespace(upstream=upstream, state=state)
    deployment_slot.reset_cache()


def test_live_slot_runs_jobs(slot):
    slot.upstream.write_text(LIVE_BLUE)
    assert deployment_slot.active_slot() == "blue"
    assert deployment_slot.is_serving_slot() is True


def test_standby_slot_skips_jobs(slot):
    slot.upstream.write_text(LIVE_GREEN)
    assert deployment_slot.is_serving_slot() is False


def test_cutover_is_picked_up_after_cache_expiry(slot, monkeypatch):
    slot.upstream.write_text(LIVE_BLUE)
    clock = iter([100.0, 101.0, 200.0])
    monkeypatch.setattr(deployment_slot.time, "monotonic", lambda: next(clock))
    assert deployment_slot.is_serving_slot() is True
    slot.upstream.write_text(LIVE_GREEN)
    assert deployment_slot.is_serving_slot() is True  # cached
    assert deployment_slot.is_serving_slot() is False


@pytest.mark.parametrize("instance,content", [
    ("", LIVE_GREEN),          # single-instance install: no slot configured
    ("blue", None),            # upstream file missing
    ("blue", "garbage\n"),     # unrecognised upstream file
])
def test_unknown_state_keeps_running_jobs(slot, instance, content):
    slot.state["instance"] = instance
    if content is not None:
        slot.upstream.write_text(content)
    assert deployment_slot.is_serving_slot() is True


@pytest.mark.anyio("asyncio")
async def test_scheduler_gates_async_and_sync_jobs(monkeypatch):
    calls: list[str] = []

    async def async_job(value):
        calls.append(f"async:{value}")
        return "done"

    def sync_job(value):
        calls.append(f"sync:{value}")
        return "done"

    serving = {"value": False}
    monkeypatch.setattr(deployment_slot, "is_serving_slot", lambda: serving["value"])
    gated_async = scheduler_module._slot_gated(async_job)
    gated_sync = scheduler_module._slot_gated(sync_job)

    assert await gated_async(1) is None
    assert gated_sync(1) is None
    assert calls == []

    serving["value"] = True
    assert await gated_async(2) == "done"
    assert gated_sync(2) == "done"
    assert calls == ["async:2", "sync:2"]
    assert scheduler_module.inspect.iscoroutinefunction(gated_async)
    assert not scheduler_module.inspect.iscoroutinefunction(gated_sync)
    assert scheduler_module._slot_gated(gated_async) is gated_async


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_scheduler_service_wraps_every_registered_job():
    service = scheduler_module.SchedulerService()

    async def job():
        return None

    registered = service._scheduler.add_job(job, "interval", seconds=60, id="probe")

    assert getattr(registered.func, "_slot_gated", False) is True
