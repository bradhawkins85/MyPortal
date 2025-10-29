"""Helpers for inspecting operating system update state.

The IMAP synchronisation routine and other background tasks can consult this
module to determine whether maintenance operations such as package upgrades
have requested a service restart. When the restart flag is present we avoid
running potentially disruptive integrations until the deployment finishes.
"""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RESTART_FLAG_PATH = _PROJECT_ROOT / "var" / "state" / "restart_required.flag"


def is_restart_pending() -> bool:
    """Return ``True`` when the system restart flag is present."""

    try:
        return _RESTART_FLAG_PATH.exists()
    except OSError:
        # When the flag directory is inaccessible we behave defensively and
        # assume a restart is required. The scheduler will retry once the
        # deployment finishes and permissions stabilise.
        return True

