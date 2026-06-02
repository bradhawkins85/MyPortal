"""Tests for system_state helpers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import app.services.system_state as system_state_module
from app.services.system_state import get_default_upgrade_mode, get_upgrade_status, is_restart_pending


def test_is_restart_pending_flag_absent(tmp_path, monkeypatch):
    """When the flag file does not exist, is_restart_pending returns False."""
    flag_path = tmp_path / "system_update.flag"
    # Ensure it does not exist
    assert not flag_path.exists()
    monkeypatch.setattr(system_state_module, "_SYSTEM_UPDATE_FLAG_PATH", flag_path)
    assert is_restart_pending() is False


def test_is_restart_pending_flag_present(tmp_path, monkeypatch):
    """When the flag file exists, is_restart_pending returns True."""
    flag_path = tmp_path / "system_update.flag"
    flag_path.touch()
    monkeypatch.setattr(system_state_module, "_SYSTEM_UPDATE_FLAG_PATH", flag_path)
    assert is_restart_pending() is True


def test_is_restart_pending_oserror_returns_true(monkeypatch):
    """When Path.exists raises OSError, is_restart_pending returns True defensively."""
    mock_path = MagicMock(spec=Path)
    mock_path.exists.side_effect = OSError("permission denied")
    monkeypatch.setattr(system_state_module, "_SYSTEM_UPDATE_FLAG_PATH", mock_path)
    assert is_restart_pending() is True


def test_is_restart_pending_flag_then_removed(tmp_path, monkeypatch):
    """Removing the flag file makes is_restart_pending return False again."""
    flag_path = tmp_path / "system_update.flag"
    flag_path.touch()
    monkeypatch.setattr(system_state_module, "_SYSTEM_UPDATE_FLAG_PATH", flag_path)
    assert is_restart_pending() is True

    flag_path.unlink()
    assert is_restart_pending() is False


def test_get_default_upgrade_mode_uses_graceful_by_default(monkeypatch):
    monkeypatch.delenv("APP_UPGRADE_MODE", raising=False)
    assert get_default_upgrade_mode() == "graceful"


def test_get_upgrade_status_reads_flag_and_status_files(tmp_path, monkeypatch):
    flag_path = tmp_path / "system_update.flag"
    status_path = tmp_path / "system_update.status"
    flag_path.write_text(
        "requested_mode=rolling\nrequested_reason=deployment_topology_changed\nrequested_at=2026-01-01T00:00:00+00:00\n",
        encoding="utf-8",
    )
    status_path.write_text(
        "status=succeeded\nmode=restart\nreason=dependency_manifest_changed\nmessage=Upgrade applied.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(system_state_module, "_SYSTEM_UPDATE_FLAG_PATH", flag_path)
    monkeypatch.setattr(system_state_module, "_SYSTEM_UPDATE_STATUS_PATH", status_path)
    monkeypatch.setenv("APP_UPGRADE_MODE", "graceful")

    status = get_upgrade_status()

    assert status["pending"] is True
    assert status["configured_mode"] == "graceful"
    assert status["requested_mode"] == "rolling"
    assert status["requested_reason"] == "deployment_topology_changed"
    assert status["last_status"] == "succeeded"
    assert status["last_mode"] == "restart"
    assert status["last_reason"] == "dependency_manifest_changed"
