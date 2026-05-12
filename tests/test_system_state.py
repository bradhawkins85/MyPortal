"""Tests for system_state helpers (is_restart_pending)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import app.services.system_state as system_state_module
from app.services.system_state import is_restart_pending


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
