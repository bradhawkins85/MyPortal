"""A pinned/dirty checkout must not run old deployment logic on a new release."""
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/upgrade.sh").read_text()


def test_fetched_coordinator_preserves_control_edits_and_reacquires_upgrade_lock(tmp_path):
    control = tmp_path / "control"
    (control / "scripts/lib").mkdir(parents=True)
    (control / "app/services").mkdir(parents=True)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=control, text=True).strip()

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (control / "scripts/upgrade.sh").write_text("old coordinator\n")
    (control / "scripts/lib/python_runtime.sh").write_text("target library\n")
    (control / "app/services/deployment_plan.py").write_text("# target planner\n")
    git("add", ".")
    git("commit", "-qm", "old")
    old = git("rev-parse", "HEAD")
    target_script = r'''#!/usr/bin/env bash
set -euo pipefail
exec 9>"$UPDATER_STATE_DIR/upgrade.lock"
flock -n 9 || exit 12
python3 - "$@" <<'PY'
import json, os, sys
print(json.dumps({"control": os.environ["MYPORTAL_CONTROL_ROOT"],
                  "environment": os.environ["MYPORTAL_ENV_FILE"],
                  "revision": os.environ["MYPORTAL_UPGRADE_REEXEC"],
                  "args": sys.argv[1:]}))
PY
'''
    (control / "scripts/upgrade.sh").write_text(target_script)
    git("commit", "-qam", "target")
    target = git("rev-parse", "HEAD")
    git("checkout", "-qb", "pinned", old)
    (control / "scripts/upgrade.sh").write_text("local edits to retain\n")
    updater = tmp_path / "updater"
    updater.mkdir(mode=0o700)
    env_file = tmp_path / "production.env"
    env_file.write_text("DB_HOST=localhost\n")
    function = SCRIPT[SCRIPT.index("run_target_upgrade_coordinator() {"):
                      SCRIPT.index("\nprovision_gitea_for_revision()")]
    result = subprocess.run(
        ["bash", "-c", "set -Eeuo pipefail\n" + function + r'''
exec 9>"$UPDATER_STATE_DIR/upgrade.lock"
flock 9
UPGRADE_ARGS=(--rolling)
run_target_upgrade_coordinator "$TARGET"
'''], cwd=control, capture_output=True, text=True, check=False, timeout=15,
        env={**os.environ, "PROJECT_ROOT": str(control), "ENV_FILE": str(env_file),
             "UPDATER_STATE_DIR": str(updater), "TARGET": target},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "control": str(control), "environment": str(env_file),
        "revision": target, "args": ["--rolling"],
    }
    assert (control / "scripts/upgrade.sh").read_text() == "local edits to retain\n"
    assert git("rev-parse", "HEAD") == old
    staged = list(updater.glob("coordinator-*"))
    assert len(staged) == 1
    assert (staged[0] / "scripts/lib/python_runtime.sh").read_text() == "target library\n"
    assert (staged[0] / "app/services/deployment_plan.py").read_text() == "# target planner\n"


def test_handoff_precedes_migrations_and_keeps_wrapper_control_context():
    flow = SCRIPT[SCRIPT.index("validate_required_configuration\ngit fetch"):]
    assert flow.index('run_target_upgrade_coordinator "$TARGET_REVISION"') < flow.index('run_migration_phase "$RELEASE_DIR"')
    assert 'export MYPORTAL_CONTROL_ROOT=%q' in SCRIPT
    assert 'export MYPORTAL_ENV_FILE=%q' in SCRIPT
    assert 'PROJECT_ROOT=${MYPORTAL_CONTROL_ROOT:-' in SCRIPT
