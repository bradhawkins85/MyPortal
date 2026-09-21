"""Atomic, phase-aware state for coordinated application upgrades."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STATE_DIR = _PROJECT_ROOT / "var" / "state"
_SYSTEM_UPDATE_FLAG_PATH = _STATE_DIR / "system_update.flag"
_SYSTEM_UPDATE_STATUS_PATH = _STATE_DIR / "system_update.status"  # legacy/admin history
_UPGRADE_STATE_PATH = _STATE_DIR / "upgrade-state.json"
_DEFAULT_UPGRADE_MODE = "graceful"
_VALID_UPGRADE_MODES = {"graceful", "rolling", "restart"}
_ACTIVE_PHASES = {"preparing", "migrating", "draining", "restarting", "verifying"}
_MAINTENANCE_PHASES = {"draining", "restarting"}
_TERMINAL_PHASES = {"succeeded", "failed", "rolled_back", "interrupted"}
_STALE_SECONDS = 30 * 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _normalise_upgrade_mode(value: str | None) -> str:
    mode = (value or "").strip().lower()
    return mode if mode in _VALID_UPGRADE_MODES else _DEFAULT_UPGRADE_MODE


def _read_key_value_file(path: Path) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    values = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _read_state() -> dict[str, Any]:
    try:
        value = json.loads(_UPGRADE_STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}


def write_upgrade_state(
    *, upgrade_id: str, target_revision: str, phase: str, message: str,
    mode: str, outcome: str | None = None, started_at: str | None = None,
) -> dict[str, Any]:
    """Atomically replace the shared state; readers never see partial JSON."""
    if phase not in _ACTIVE_PHASES | _TERMINAL_PHASES:
        raise ValueError("invalid upgrade phase")
    previous = _read_state()
    now = _iso_now()
    state = {
        "schema_version": 1,
        "upgrade_id": upgrade_id,
        "target_revision": target_revision,
        "phase": phase,
        "started_at": started_at or previous.get("started_at") or now,
        "updated_at": now,
        "finished_at": now if phase in _TERMINAL_PHASES else None,
        "message": message[:500],
        "mode": _normalise_upgrade_mode(mode),
        "maintenance": phase in _MAINTENANCE_PHASES,
        "outcome": outcome if phase in _TERMINAL_PHASES else None,
    }
    _UPGRADE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".upgrade-state-", dir=_UPGRADE_STATE_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)  # nginx's unprivileged worker must read it
        os.replace(temporary, _UPGRADE_STATE_PATH)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return state


def get_public_upgrade_state(*, recover_stale: bool = True) -> dict[str, Any]:
    """Return only browser-safe state, recovering abandoned active upgrades."""
    state = _read_state()
    if not state:
        return {"phase": "idle", "maintenance": False, "outcome": None}
    if state.get("phase") in _ACTIVE_PHASES:
        try:
            updated = datetime.fromisoformat(str(state["updated_at"]).replace("Z", "+00:00"))
            stale = _utc_now() - updated.astimezone(timezone.utc) > timedelta(
                seconds=int(os.getenv("UPGRADE_STATE_STALE_SECONDS", _STALE_SECONDS))
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            stale = True
        if stale:
            if recover_stale:
                state = write_upgrade_state(
                    upgrade_id=str(state.get("upgrade_id", "unknown")),
                    target_revision=str(state.get("target_revision", "unknown")),
                    phase="interrupted", message="Upgrade was interrupted; normal service has resumed.",
                    mode=str(state.get("mode", _DEFAULT_UPGRADE_MODE)), outcome="interrupted",
                    started_at=state.get("started_at"),
                )
            else:
                state = {**state, "phase": "interrupted", "maintenance": False, "outcome": "interrupted"}
    allowed = ("schema_version", "upgrade_id", "target_revision", "phase", "started_at",
               "updated_at", "finished_at", "message", "mode", "maintenance", "outcome")
    return {key: state.get(key) for key in allowed}


def maintenance_is_active() -> bool:
    return bool(get_public_upgrade_state().get("maintenance"))


def get_default_upgrade_mode() -> str:
    return _normalise_upgrade_mode(os.getenv("APP_UPGRADE_MODE"))


def get_upgrade_status() -> dict[str, Any]:
    pending = is_restart_pending()
    flag_values = _read_key_value_file(_SYSTEM_UPDATE_FLAG_PATH)
    status_values = _read_key_value_file(_SYSTEM_UPDATE_STATUS_PATH)
    configured_mode = get_default_upgrade_mode()
    current = get_public_upgrade_state()
    return {
        "configured_mode": configured_mode, "pending": pending,
        "requested_mode": _normalise_upgrade_mode(flag_values.get("requested_mode")) if flag_values else configured_mode,
        "requested_reason": flag_values.get("requested_reason", ""),
        "requested_plan": flag_values.get("deployment_plan", ""),
        "requested_at": flag_values.get("requested_at", ""),
        "requested_from_ui": flag_values.get("requested_from_ui", ""),
        "last_status": status_values.get("status", ""),
        "last_mode": _normalise_upgrade_mode(status_values.get("requested_mode") or status_values.get("mode")) if status_values else configured_mode,
        "last_reason": status_values.get("reason", ""), "last_plan": status_values.get("deployment_plan", ""),
        "last_message": status_values.get("message", ""),
        "last_started_at": status_values.get("started_at", ""), "last_finished_at": status_values.get("finished_at", ""),
        "last_ready_wait_seconds": status_values.get("ready_wait_seconds", ""), "coordinator": current,
    }


def is_restart_pending() -> bool:
    try:
        return _SYSTEM_UPDATE_FLAG_PATH.exists()
    except OSError:
        return True
