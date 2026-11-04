"""Reports from the overseer that keeps a device's tray and RMM agent connected.

The overseer is a separate service on each device (see ``tray/internal/overseer``).
Every few minutes it posts what it saw of the tray service and RMM agent, and
any recovery it took, to ``POST /api/tray/overseer/report`` with the tray
device's token. MyPortal answers with how long ago it last heard from the
tray's WebSocket and from the RMM agent; the overseer restarts an agent that
is running but has stopped reaching MyPortal.

Ages are worked out here so a device with a wrong clock still decides
correctly. Every recovery is written to the audit log against the tray device.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.core.logging import log_warning
from app.repositories import rmm as rmm_repo
from app.repositories import tray as tray_repo
from app.services import audit as audit_service

# How often overseers report. The overseer accepts 60–3600 seconds.
CHECK_INTERVAL_SECONDS = 300


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def age_seconds(value: Any, *, now: datetime | None = None) -> int | None:
    """Whole seconds since ``value``; ``None`` when it was never recorded."""

    seen = _as_utc(value)
    if seen is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0, int((current - seen).total_seconds()))


def connectivity(
    device: Mapping[str, Any], rmm_agent: Mapping[str, Any] | None, *, now: datetime | None = None
) -> dict[str, Any]:
    """MyPortal's view of the device's tray WebSocket and RMM agent."""

    return {
        "tray": {"enrolled": True, "last_seen_age_seconds": age_seconds(device.get("ws_last_seen_utc"), now=now)},
        "rmm": {
            "enrolled": rmm_agent is not None,
            "last_seen_age_seconds": age_seconds(rmm_agent.get("last_seen_utc"), now=now) if rmm_agent else None,
        },
        "check_interval_seconds": CHECK_INTERVAL_SECONDS,
    }


async def record_report(device: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    """Store the overseer's report, audit its recoveries and return the device's status."""

    device_id = int(device["id"])
    await tray_repo.record_overseer_report(device_id, overseer_version=report.get("overseer_version"))
    for action in report.get("actions") or []:
        occurred_at = _as_utc(action.get("occurred_at"))
        try:
            await audit_service.record(
                action="tray.overseer.recover",
                entity_type="tray_device",
                entity_id=device_id,
                metadata={
                    "service": action.get("service"),
                    "recovery": action.get("action"),
                    "reason": action.get("reason") or "",
                    "succeeded": bool(action.get("succeeded")),
                    "error": action.get("error") or None,
                    "occurred_at": occurred_at.isoformat() if occurred_at else None,
                    "company_id": device.get("company_id"),
                    "asset_id": device.get("asset_id"),
                    "overseer_version": report.get("overseer_version"),
                },
                source="background",
                actor="overseer",
            )
        except Exception as exc:  # noqa: BLE001 - one bad audit row must not fail the report
            log_warning("Failed to audit an overseer recovery", device_id=device_id, error=str(exc))
    rmm_agent = await rmm_repo.get_tray_device_agent(device_id)
    return connectivity(device, rmm_agent)
