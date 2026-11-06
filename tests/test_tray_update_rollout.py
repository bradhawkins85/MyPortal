from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app import main
from app.repositories import tray as tray_repo
from app.services import tray as tray_service
from app.services import tray_update_rollout as rollout


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fast_rollout(monkeypatch):
    monkeypatch.setattr(rollout, "POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(rollout, "PICKUP_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(rollout, "UPDATE_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(rollout, "_state", None)
    monkeypatch.setattr(rollout, "_task", None)


class FakeFleet:
    """Devices that report the new version a few polls after receiving the update."""

    def __init__(self, devices: list[dict[str, Any]], *, polls_to_update: int = 2):
        self.devices = {d["id"]: dict(d) for d in devices}
        self.polls_to_update = polls_to_update
        self.pending: dict[int, int] = {}
        self.events: list[tuple[str, int]] = []
        self.commands: dict[int, dict[str, Any]] = {}

    async def get_device_by_id(self, device_id: int) -> dict[str, Any] | None:
        if device_id in self.pending:
            self.pending[device_id] -= 1
            if self.pending[device_id] <= 0:
                del self.pending[device_id]
                self.devices[device_id]["agent_version"] = "2.0.0"
                self.events.append(("updated", device_id))
        device = self.devices.get(device_id)
        return dict(device) if device else None

    async def dispatch(self, device, *, initiated_by_user_id, extra_payload):
        device_id = int(device["id"])
        # The previous device must have finished before the next one starts.
        assert not self.pending, "an update was sent while another was in progress"
        self.events.append(("sent", device_id))
        command_id = 100 + device_id
        online = self.devices[device_id].get("online", True)
        self.commands[command_id] = {"status": "delivered" if online else "queued"}
        if online:
            self.pending[device_id] = self.polls_to_update
        return command_id, online

    async def get_command(self, command_id: int) -> dict[str, Any] | None:
        return self.commands.get(command_id)


def _install(monkeypatch, fleet: FakeFleet) -> None:
    monkeypatch.setattr(tray_repo, "get_device_by_id", fleet.get_device_by_id)
    monkeypatch.setattr(tray_repo, "get_command", fleet.get_command)
    monkeypatch.setattr(tray_service, "dispatch_update_command", fleet.dispatch)


@pytest.mark.anyio
async def test_rollout_updates_devices_one_at_a_time(monkeypatch):
    monkeypatch.setattr(rollout, "UPDATE_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(rollout, "PICKUP_TIMEOUT_SECONDS", 60)
    fleet = FakeFleet(
        [
            {"id": 1, "device_uid": "u1", "status": "active", "agent_version": "1.0.0"},
            {"id": 2, "device_uid": "u2", "status": "active", "agent_version": "1.0.0"},
            {"id": 3, "device_uid": "u3", "status": "active", "agent_version": "1.5.0"},
        ]
    )
    _install(monkeypatch, fleet)

    started = rollout.start_rollout(
        list(fleet.devices.values()),
        target_version="2.0.0",
        initiated_by_user_id=1,
        is_outdated=main._is_tray_agent_outdated,
    )
    assert started
    await asyncio.wait_for(rollout._task, timeout=5)

    assert fleet.events == [
        ("sent", 1), ("updated", 1),
        ("sent", 2), ("updated", 2),
        ("sent", 3), ("updated", 3),
    ]
    state = rollout.rollout_status()
    assert state is not None and not state.running
    assert (state.processed, state.updated) == (3, 3)


@pytest.mark.anyio
async def test_rollout_refuses_to_start_twice(monkeypatch):
    blocker = asyncio.Event()

    async def slow_run(*args, **kwargs):
        await blocker.wait()

    monkeypatch.setattr(rollout, "_run_rollout", slow_run)
    kwargs = dict(target_version="2.0.0", initiated_by_user_id=1, is_outdated=main._is_tray_agent_outdated)
    assert rollout.start_rollout([{"id": 1}], **kwargs)
    assert not rollout.start_rollout([{"id": 2}], **kwargs)
    blocker.set()
    await rollout._task


@pytest.mark.anyio
async def test_offline_device_is_left_queued_and_rollout_continues(monkeypatch):
    fleet = FakeFleet(
        [
            {"id": 1, "device_uid": "u1", "status": "active", "agent_version": "1.0.0", "online": False},
            {"id": 2, "device_uid": "u2", "status": "active", "agent_version": "1.0.0"},
        ],
        polls_to_update=1,
    )
    _install(monkeypatch, fleet)
    monkeypatch.setattr(rollout, "UPDATE_TIMEOUT_SECONDS", 60)

    rollout.start_rollout(
        list(fleet.devices.values()),
        target_version="2.0.0",
        initiated_by_user_id=None,
        is_outdated=main._is_tray_agent_outdated,
    )
    await asyncio.wait_for(rollout._task, timeout=5)

    assert fleet.events == [("sent", 1), ("sent", 2), ("updated", 2)]
    assert fleet.commands[101]["status"] == "queued"
    state = rollout.rollout_status()
    assert (state.queued_offline, state.updated) == (1, 1)


@pytest.mark.anyio
async def test_device_that_never_reports_times_out(monkeypatch):
    fleet = FakeFleet(
        [{"id": 1, "device_uid": "u1", "status": "active", "agent_version": "1.0.0"}],
        polls_to_update=10**6,
    )
    _install(monkeypatch, fleet)

    rollout.start_rollout(
        list(fleet.devices.values()),
        target_version="2.0.0",
        initiated_by_user_id=None,
        is_outdated=main._is_tray_agent_outdated,
    )
    await asyncio.wait_for(rollout._task, timeout=5)

    assert rollout.rollout_status().timed_out == 1


@pytest.mark.anyio
async def test_skips_devices_revoked_or_updated_since_the_click(monkeypatch):
    fleet = FakeFleet(
        [
            {"id": 1, "device_uid": "u1", "status": "revoked", "agent_version": "1.0.0"},
            {"id": 2, "device_uid": "u2", "status": "active", "agent_version": "2.0.0"},
        ]
    )
    _install(monkeypatch, fleet)
    dispatch = AsyncMock()
    monkeypatch.setattr(tray_service, "dispatch_update_command", dispatch)

    rollout.start_rollout(
        [{"id": 1}, {"id": 2}, {"id": 99}],
        target_version="2.0.0",
        initiated_by_user_id=None,
        is_outdated=main._is_tray_agent_outdated,
    )
    await asyncio.wait_for(rollout._task, timeout=5)

    dispatch.assert_not_called()
    assert rollout.rollout_status().skipped == 3
