"""Helpers for inspecting operating system update state.

The IMAP synchronisation routine and other background tasks can consult this
module to determine whether maintenance operations such as package upgrades
have been scheduled. When the update flag is present we avoid
running potentially disruptive integrations until the deployment finishes.
"""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SYSTEM_UPDATE_FLAG_PATH = _PROJECT_ROOT / "var" / "state" / "system_update.flag"


def is_restart_pending() -> bool:
    """Return ``True`` when a system update is queued for processing."""

    try:
        return _SYSTEM_UPDATE_FLAG_PATH.exists()
    except OSError:
        # When the flag directory is inaccessible we behave defensively and
        # assume a restart is required. The scheduler will retry once the
        # deployment finishes and permissions stabilise.
        return True
