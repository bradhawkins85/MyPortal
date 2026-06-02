"""Helpers for inspecting operating system update state.

The IMAP synchronisation routine and other background tasks can consult this
module to determine whether maintenance operations such as package upgrades
have been scheduled. When the update flag is present we avoid running
potentially disruptive integrations until the deployment finishes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STATE_DIR = _PROJECT_ROOT / "var" / "state"
_SYSTEM_UPDATE_FLAG_PATH = _PROJECT_ROOT / "var" / "state" / "system_update.flag"
_SYSTEM_UPDATE_STATUS_PATH = _PROJECT_ROOT / "var" / "state" / "system_update.status"
_DEFAULT_UPGRADE_MODE = "graceful"
_VALID_UPGRADE_MODES = {"graceful", "rolling", "restart"}


def _normalise_upgrade_mode(value: str | None) -> str:
    if not value:
        return _DEFAULT_UPGRADE_MODE
    mode = value.strip().lower()
    return mode if mode in _VALID_UPGRADE_MODES else _DEFAULT_UPGRADE_MODE


def _read_key_value_file(path: Path) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}

    values: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def get_default_upgrade_mode() -> str:
    """Return the configured deployment mode for non-pack updates."""

    return _normalise_upgrade_mode(os.getenv("APP_UPGRADE_MODE"))


def get_upgrade_status() -> dict[str, Any]:
    """Return the current/past system update status for admin views."""

    pending = is_restart_pending()
    flag_values = _read_key_value_file(_SYSTEM_UPDATE_FLAG_PATH)
    status_values = _read_key_value_file(_SYSTEM_UPDATE_STATUS_PATH)
    configured_mode = get_default_upgrade_mode()

    requested_mode = _normalise_upgrade_mode(flag_values.get("requested_mode"))
    last_mode = _normalise_upgrade_mode(
        status_values.get("requested_mode") or status_values.get("mode")
    )

    if not flag_values:
        requested_mode = configured_mode
    if not status_values:
        last_mode = configured_mode

    return {
        "configured_mode": configured_mode,
        "pending": pending,
        "requested_mode": requested_mode,
        "requested_reason": flag_values.get("requested_reason", ""),
        "requested_at": flag_values.get("requested_at", ""),
        "requested_from_ui": flag_values.get("requested_from_ui", ""),
        "last_status": status_values.get("status", ""),
        "last_mode": last_mode,
        "last_reason": status_values.get("reason", ""),
        "last_message": status_values.get("message", ""),
        "last_started_at": status_values.get("started_at", ""),
        "last_finished_at": status_values.get("finished_at", ""),
        "last_ready_wait_seconds": status_values.get("ready_wait_seconds", ""),
    }


def is_restart_pending() -> bool:
    """Return ``True`` when a system update is queued for processing."""

    try:
        return _SYSTEM_UPDATE_FLAG_PATH.exists()
    except OSError:
        # When the flag directory is inaccessible we behave defensively and
        # assume a restart is required. The scheduler will retry once the
        # deployment finishes and permissions stabilise.
        return True
