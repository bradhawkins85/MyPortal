import json
import os

import pytest

from app.core.config import Settings
from app.core.env_reload import RESTART_REQUIRED_FIELDS, EnvReloader


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A base env file loaded into os.environ, as systemd does at start."""

    base = tmp_path / "myportal.env"
    values = {"SMTP_HOST": "old.example.com", "EVENT_LOG_DEFAULT_LOGS": "Application;System", "DB_HOST": "db1"}
    base.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    settings = Settings()
    reloader = EnvReloader(
        paths=[base, tmp_path / "myportal.blue.env"],
        settings_getter=lambda: settings,
        state_dir=tmp_path / "state",
    )
    assert reloader.prime()
    return base, settings, reloader, tmp_path


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def _apply(reloader):
    # A change is applied only once the file is unchanged for one poll.
    assert reloader.poll() is None
    return reloader.poll()


def test_live_setting_is_applied_to_the_shared_settings_object(env):
    base, settings, reloader, _ = env
    _write(base, "SMTP_HOST=new.example.com\nEVENT_LOG_DEFAULT_LOGS=Application;System;Security\nDB_HOST=db1\n")

    result = _apply(reloader)

    assert result.applied == ["EVENT_LOG_DEFAULT_LOGS", "SMTP_HOST"]
    assert result.restart_required == []
    assert settings.smtp_host == "new.example.com"
    assert settings.event_log_default_logs == "Application;System;Security"
    assert os.environ["SMTP_HOST"] == "new.example.com"
    assert reloader.poll() is None


def test_restart_required_setting_is_reported_not_applied(env):
    base, settings, reloader, _ = env
    _write(base, "SMTP_HOST=old.example.com\nEVENT_LOG_DEFAULT_LOGS=Application;System\nDB_HOST=db2\n")

    result = _apply(reloader)

    assert result.applied == []
    assert result.restart_required == ["DB_HOST"]
    assert settings.database_host == "db1"
    assert os.environ["DB_HOST"] == "db1"
    assert "database_host" in RESTART_REQUIRED_FIELDS

    # Changing it back clears the pending restart.
    _write(base, "SMTP_HOST=old.example.com\nEVENT_LOG_DEFAULT_LOGS=Application;System\nDB_HOST=db1\n")
    assert _apply(reloader).restart_required == []


def test_invalid_file_keeps_current_settings_without_logging_values(env):
    base, settings, reloader, tmp_path = env
    _write(base, "SMTP_HOST=new.example.com\nSMTP_PORT=not-a-port-secret\nEVENT_LOG_DEFAULT_LOGS=Application;System\nDB_HOST=db1\n")

    result = _apply(reloader)

    assert "smtp_port" in result.error.lower()
    assert "not-a-port-secret" not in result.error
    assert settings.smtp_host == "old.example.com"
    assert os.environ["SMTP_HOST"] == "old.example.com"
    assert "SMTP_PORT" not in os.environ
    assert reloader.poll() is None  # not retried until the file changes again

    _write(base, "SMTP_HOST=new.example.com\nSMTP_PORT=2525\nEVENT_LOG_DEFAULT_LOGS=Application;System\nDB_HOST=db1\n")
    result = _apply(reloader)
    assert result.error is None
    assert (settings.smtp_host, settings.smtp_port) == ("new.example.com", 2525)


def test_removed_key_returns_to_its_default(env):
    base, settings, reloader, _ = env
    _write(base, "SMTP_HOST=old.example.com\nDB_HOST=db1\n")

    assert _apply(reloader).applied == ["EVENT_LOG_DEFAULT_LOGS"]
    assert "EVENT_LOG_DEFAULT_LOGS" not in os.environ
    assert settings.event_log_default_logs == Settings.model_fields["event_log_default_logs"].default


def test_slot_file_overrides_the_base_file(env):
    base, settings, reloader, tmp_path = env
    _write(tmp_path / "myportal.blue.env", "SMTP_HOST=blue.example.com\n")

    assert _apply(reloader).applied == ["SMTP_HOST"]
    assert settings.smtp_host == "blue.example.com"

    # A base change to a key the slot file sets changes nothing.
    _write(base, "SMTP_HOST=base.example.com\nEVENT_LOG_DEFAULT_LOGS=Application;System\nDB_HOST=db1\n")
    assert _apply(reloader).applied == []
    assert settings.smtp_host == "blue.example.com"


def test_status_records_key_names_and_file_digests_only(env):
    base, _, reloader, tmp_path = env
    _write(base, "SMTP_HOST=new.example.com\nSMTP_PASS=hunter2\nEVENT_LOG_DEFAULT_LOGS=Application;System\nDB_HOST=db1\n")
    _apply(reloader)

    (status,) = (tmp_path / "state").glob("*.json")
    text = status.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["applied"] == ["SMTP_HOST", "SMTP_PASS"]
    assert payload["pid"] == os.getpid()
    assert payload["files"][str(base)] is not None
    assert payload["files"][str(tmp_path / "myportal.blue.env")] is None
    assert "hunter2" not in text and "new.example.com" not in text


def test_missing_env_file_disables_reload(tmp_path):
    reloader = EnvReloader(paths=[tmp_path / "absent.env"], state_dir=tmp_path / "state")
    assert reloader.prime() is False
    assert reloader.poll() is None


def _run_reload_cli(shared_root, *args):
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "myportal-baremetal.sh"
    return subprocess.run(
        ["bash", str(script), "reload", "--timeout", "1", *args],
        env={
            **os.environ,
            "MYPORTAL_SHARED_ROOT": str(shared_root),
            "MYPORTAL_ENV_FILE": str(shared_root / "myportal.env"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_reload_command_confirms_workers_and_lists_restart_settings(env):
    base, _, reloader, tmp_path = env
    reloader._state_dir = tmp_path / "state" / "env-reload"
    _write(base, "SMTP_HOST=old.example.com\nEVENT_LOG_DEFAULT_LOGS=Application;System\nDB_HOST=db2\n")
    _apply(reloader)

    result = _run_reload_cli(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "current" in result.stdout
    assert "DB_HOST" in result.stdout


def test_reload_command_reports_pending_and_rejected_files(env):
    base, _, reloader, tmp_path = env
    reloader._state_dir = tmp_path / "state" / "env-reload"
    reloader._write_status()
    _write(base, "SMTP_PORT=bad\n")

    pending = _run_reload_cli(tmp_path)
    assert pending.returncode == 1
    assert "pending" in pending.stdout

    _apply(reloader)
    rejected = _run_reload_cli(tmp_path)
    assert rejected.returncode == 1
    assert "rejected" in rejected.stdout and "bad" not in rejected.stdout


def test_reload_command_only_hashes_the_configured_env_files(env):
    _, _, reloader, tmp_path = env
    reloader._paths = [tmp_path / "elsewhere.env"]
    reloader._state_dir = tmp_path / "state" / "env-reload"
    reloader._snapshot.digests = {"/etc/shadow": "0" * 64}
    reloader._write_status()

    result = _run_reload_cli(tmp_path)

    assert result.returncode == 1
    assert "unrecognised" in result.stdout
