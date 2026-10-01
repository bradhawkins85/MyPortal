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


def test_symlinked_record_is_never_read_or_rewritten(monkeypatch, tmp_path):
    import json

    import pytest

    history = tmp_path / "history"
    history.mkdir()
    monkeypatch.setattr(system_update_history, "_HISTORY_DIR", history)
    update_id = "0b5a3c1e-9a0f-4d7e-8a3b-5d2f1c0e9a7b"
    secret = tmp_path / "root-only.json"
    secret.write_text(json.dumps({"id": update_id, "status": "running", "output": "secret"}))
    (history / f"{update_id}.json").symlink_to(secret)

    with pytest.raises(KeyError):
        system_update_history.get(update_id)
    with pytest.raises(KeyError):
        system_update_history.update(update_id, status="succeeded", output="x")
    assert system_update_history.list_updates() == []
    assert "secret" in secret.read_text()


def test_symlinked_history_directory_is_refused(monkeypatch, tmp_path):
    import pytest

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "history"
    link.symlink_to(elsewhere)
    monkeypatch.setattr(system_update_history, "_HISTORY_DIR", link)

    with pytest.raises(OSError):
        system_update_history.create_pending(
            requested_at="2026-09-22T01:00:00+00:00", target_revision="abc", source="manual",
        )
    assert list(elsewhere.iterdir()) == []


def test_records_are_private_to_the_writer(monkeypatch, tmp_path):
    monkeypatch.setattr(system_update_history, "_HISTORY_DIR", tmp_path)
    record = system_update_history.create_pending(
        requested_at="2026-09-22T01:00:00+00:00", target_revision="abc", source="manual",
    )
    path = tmp_path / f"{record['id']}.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert [p.name for p in tmp_path.iterdir()] == [path.name]
