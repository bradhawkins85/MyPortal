"""Required maintenance must pause collection before destructive migrations."""
import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.database import Database
from app.core.event_log_database import event_log_db


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/upgrade.sh").read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("state,configured,expected", [
    (None, True, True), ("failed", True, True),
    ("completed", True, False), (None, False, False),
])
async def test_preflight_checks_pending_configured_migrations(tmp_path, monkeypatch, state, configured, expected):
    name = "528_rmm_event_log_repeats_index.sql"
    (tmp_path / name).write_bytes((ROOT / "migrations" / name).read_bytes())
    rows = [] if state is None else [(name, state)]
    cursor = SimpleNamespace(fetchall=AsyncMock(return_value=rows))
    connection = SimpleNamespace(execute=AsyncMock(return_value=cursor))

    @asynccontextmanager
    async def acquire():
        yield connection

    database = Database()
    database._use_sqlite = True
    monkeypatch.setattr(database, "connect", AsyncMock())
    monkeypatch.setattr(database, "acquire", acquire)
    monkeypatch.setattr(database, "_ensure_migrations_table", AsyncMock())
    monkeypatch.setattr(database, "_get_migrations_dir", lambda: tmp_path)
    monkeypatch.setattr(event_log_db, "is_configured", lambda: configured)
    assert await database.migrations_require_maintenance() is expected


def _run_phase(tmp_path, *, required="true", migrate_status="0", stop_status="0", retry=False):
    functions = SCRIPT[SCRIPT.index("write_migration_maintenance_state() {"):
                       SCRIPT.index("\nis_additive_migration_only_release()")]
    (tmp_path / "shared/state").mkdir(parents=True)
    updater = tmp_path / "updater"
    updater.mkdir()
    if retry:
        (updater / "migration-maintenance.active").touch()
    harness = r'''
set -Eeuo pipefail
MIGRATION_MAINTENANCE_ACTIVE=false
ensure_updater_state_dir() { mkdir -p "$UPDATER_STATE_DIR"; }
publish_state_file() { cp "$1" "$2"; }
write_upgrade_status() { :; }
systemctl() {
  echo "systemctl $*" >> "$EVENTS"
  if [[ "$1" == stop ]]; then return "$STOP_STATUS"; fi
  return 0
}
run_release_manage() {
  if [[ "$2" == migration-maintenance-required ]]; then
    printf '%s\n' "$REQUIRED"
    return 0
  fi
  echo "migrate $*" >> "$EVENTS"
  if [[ -f "$SHARED_ROOT/state/upgrade-state.json" ]]; then
    cp "$SHARED_ROOT/state/upgrade-state.json" "$DURING"
  fi
  return "$MIGRATE_STATUS"
}
'''
    result = subprocess.run(
        ["bash", "-c", harness + functions + r'''
if run_migration_phase /release old new; then
  echo verified-cutover >> "$EVENTS"
  leave_migration_maintenance
else
  exit 1
fi
'''],
        capture_output=True, text=True, check=False,
        env={**os.environ, "SHARED_ROOT": str(tmp_path / "shared"),
             "UPDATER_STATE_DIR": str(updater), "EVENTS": str(tmp_path / "events"),
             "DURING": str(tmp_path / "during.json"), "REQUIRED": required,
             "MIGRATE_STATUS": migrate_status, "STOP_STATUS": stop_status,
             "UPG01_MAINTENANCE_MODE": "false", "TARGET_REVISION": "new",
             "UPGRADE_STARTED_AT": "2026-10-27T00:00:00Z", "ENV_FILE": "/test.env"},
    )
    events = (tmp_path / "events").read_text()
    return result, events


def test_required_maintenance_stops_workers_before_migration_and_clears_after_cutover(tmp_path):
    result, events = _run_phase(tmp_path)
    assert result.returncode == 0, result.stderr
    for service in ("myportal@blue.service", "myportal@green.service", "myportal.service"):
        assert events.index("systemctl stop " + service) < events.index("migrate ")
    assert "--maintenance" in events
    assert json.loads((tmp_path / "during.json").read_text())["maintenance"] is True
    state = json.loads((tmp_path / "shared/state/upgrade-state.json").read_text())
    assert state["maintenance"] is False and state["phase"] == "succeeded"
    assert not (tmp_path / "updater/migration-maintenance.active").exists()
    assert SCRIPT.index('run_rolling_restart "$TARGET_REVISION"') < SCRIPT.index('\nleave_migration_maintenance\n')


def test_ordinary_migrations_keep_workers_running(tmp_path):
    result, events = _run_phase(tmp_path, required="false")
    assert result.returncode == 0, result.stderr
    assert "systemctl" not in events and "--maintenance" not in events
    assert not (tmp_path / "shared/state/upgrade-state.json").exists()


def test_migration_failure_keeps_maintenance_for_retry(tmp_path):
    result, events = _run_phase(tmp_path, migrate_status="1")
    assert result.returncode == 1
    assert "verified-cutover" not in events
    assert json.loads((tmp_path / "shared/state/upgrade-state.json").read_text())["maintenance"] is True
    assert (tmp_path / "updater/migration-maintenance.active").exists()


def test_failed_cutover_retry_keeps_maintenance_even_if_migrations_completed(tmp_path):
    result, events = _run_phase(tmp_path, required="false", retry=True)
    assert result.returncode == 0, result.stderr
    assert "systemctl stop" in events and "--maintenance" in events


def test_worker_stop_failure_prevents_migration(tmp_path):
    result, events = _run_phase(tmp_path, stop_status="1")
    assert result.returncode == 1
    assert "migrate " not in events


def test_maintenance_rollback_does_not_restart_old_workers():
    rollback = SCRIPT[SCRIPT.index("rollback() {"):SCRIPT.index("\nrun_rolling_restart()")]
    assert '"${MIGRATION_MAINTENANCE_ACTIVE:-false}" == true' in rollback
    assert 'systemctl stop "myportal@${new_instance}.service"' in rollback
