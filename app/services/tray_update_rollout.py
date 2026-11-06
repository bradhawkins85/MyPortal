"""Sequential rollout of tray agent updates.

"Update all outdated agents" used to send the ``update`` command to every
outdated device at once, so they all downloaded the installer at the same
time and saturated the server and its uplink. This module updates devices
one at a time instead: it sends the command to one device, waits for that
device to report the new version (or for a timeout), then moves on.

A device that does not pick the command up within ``PICKUP_TIMEOUT_SECONDS``
is treated as offline. Its command stays queued and is delivered when it
reconnects, which spreads those devices out naturally, and the rollout
continues with the next device rather than waiting for it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.logging import log_error, log_info, log_warning
from app.repositories import tray as tray_repo
from app.services import tray as tray_service

# How often the rollout re-reads the device and its command while waiting.
POLL_INTERVAL_SECONDS = 10.0
# How long a device has to receive the queued command before it is treated as
# offline and left to pick the update up when it reconnects.
PICKUP_TIMEOUT_SECONDS = 90.0
# How long the rollout waits for a device that received the command to report
# the new version before moving on to the next device.
UPDATE_TIMEOUT_SECONDS = 600.0

OutdatedCheck = Callable[[str | None, str | None], bool]


@dataclass
class RolloutState:
    target_version: str
    total: int
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processed: int = 0
    updated: int = 0
    queued_offline: int = 0
    timed_out: int = 0
    skipped: int = 0
    current_hostname: str | None = None
    finished_at: datetime | None = None

    @property
    def running(self) -> bool:
        return self.finished_at is None


_state: RolloutState | None = None
_task: asyncio.Task[None] | None = None


def rollout_status() -> RolloutState | None:
    """Return the current or most recent rollout started on this worker."""

    return _state


def is_rollout_running() -> bool:
    return _task is not None and not _task.done()


def start_rollout(
    devices: list[dict[str, Any]],
    *,
    target_version: str,
    initiated_by_user_id: int | None,
    is_outdated: OutdatedCheck,
) -> bool:
    """Start updating ``devices`` one at a time in the background.

    Returns ``False`` (and starts nothing) when a rollout is already running.
    """

    global _state, _task
    if is_rollout_running():
        return False
    device_ids = [int(device["id"]) for device in devices]
    _state = RolloutState(target_version=target_version, total=len(device_ids))
    _task = asyncio.create_task(
        _run_rollout(
            _state,
            device_ids,
            initiated_by_user_id=initiated_by_user_id,
            is_outdated=is_outdated,
        ),
        name="tray-update-rollout",
    )
    log_info(
        "Started sequential tray update rollout",
        device_count=len(device_ids),
        target_version=target_version,
    )
    return True


async def _run_rollout(
    state: RolloutState,
    device_ids: list[int],
    *,
    initiated_by_user_id: int | None,
    is_outdated: OutdatedCheck,
) -> None:
    try:
        for device_id in device_ids:
            try:
                outcome = await update_one_device(
                    device_id,
                    state=state,
                    initiated_by_user_id=initiated_by_user_id,
                    is_outdated=is_outdated,
                )
            except Exception as exc:  # noqa: BLE001 - one device must not stop the rollout
                log_error("Tray update rollout failed for device", device_id=device_id, error=str(exc))
                outcome = "skipped"
            if outcome == "updated":
                state.updated += 1
            elif outcome == "queued":
                state.queued_offline += 1
            elif outcome == "timed_out":
                state.timed_out += 1
            else:
                state.skipped += 1
            state.processed += 1
    finally:
        state.current_hostname = None
        state.finished_at = datetime.now(timezone.utc)
        log_info(
            "Finished sequential tray update rollout",
            target_version=state.target_version,
            updated=state.updated,
            queued_offline=state.queued_offline,
            timed_out=state.timed_out,
            skipped=state.skipped,
        )


async def update_one_device(
    device_id: int,
    *,
    state: RolloutState,
    initiated_by_user_id: int | None,
    is_outdated: OutdatedCheck,
) -> str:
    """Update a single device and wait for it to finish.

    Returns ``"updated"``, ``"queued"`` (offline; command left queued),
    ``"timed_out"`` or ``"skipped"`` (revoked, already current, or an
    update was already queued for it).
    """

    target = state.target_version
    device = await tray_repo.get_device_by_id(device_id)
    if (
        not device
        or device.get("status") == "revoked"
        or not is_outdated(device.get("agent_version"), target)
    ):
        return "skipped"

    state.current_hostname = device.get("hostname") or device.get("device_uid")
    command_id, _delivered = await tray_service.dispatch_update_command(
        device,
        initiated_by_user_id=initiated_by_user_id,
        extra_payload={
            "latest_tray_version": target,
            "current_agent_version": device.get("agent_version"),
        },
    )
    if command_id is None:
        return "skipped"

    loop = asyncio.get_running_loop()
    started = loop.time()
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed = loop.time() - started
        current = await tray_repo.get_device_by_id(device_id)
        if current and not is_outdated(current.get("agent_version"), target):
            return "updated"
        command = await tray_repo.get_command(command_id)
        if command and command.get("status") == "queued" and elapsed >= PICKUP_TIMEOUT_SECONDS:
            log_info(
                "Tray device did not pick up the update; leaving it queued",
                device_id=device_id,
                command_id=command_id,
            )
            return "queued"
        if elapsed >= UPDATE_TIMEOUT_SECONDS:
            log_warning(
                "Tray device did not report the new version in time",
                device_id=device_id,
                command_id=command_id,
                target_version=target,
            )
            return "timed_out"
