from __future__ import annotations

from pathlib import Path

from app.services import system_update_history


def test_update_history_records_lifecycle_and_redacts_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(system_update_history, "_HISTORY_DIR", tmp_path)
    record = system_update_history.create_pending(
        requested_at="2026-09-22T01:00:00+00:00",
        target_revision="abc123",
        source="scheduled",
    )
    assert record["status"] == "pending"
    assert record["completed_at"] is None

    running = system_update_history.update(record["id"], status="running")
    assert running["status"] == "running"

    completed = system_update_history.update(
        record["id"], status="failed", output="token=top-secret\nfailed",
        error="password=hunter2", completed=True,
    )
    assert completed["completed_at"]
    assert "top-secret" not in completed["output"]
    assert "hunter2" not in completed["error"]
    assert system_update_history.list_updates() == [completed]


def test_invalid_update_identifier_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(system_update_history, "_HISTORY_DIR", tmp_path)
    try:
        system_update_history.get("../../secret")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal identifier was accepted")



def test_blank_history_dir_setting_uses_default(monkeypatch, tmp_path):
    monkeypatch.setenv("MYPORTAL_SYSTEM_UPDATE_HISTORY_DIR", "")
    assert system_update_history._resolve_history_dir(tmp_path) == tmp_path

    monkeypatch.setenv("MYPORTAL_SYSTEM_UPDATE_HISTORY_DIR", "/srv/history")
    assert system_update_history._resolve_history_dir(tmp_path) == Path("/srv/history")
