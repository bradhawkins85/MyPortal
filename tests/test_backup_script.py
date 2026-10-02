"""Tests for scripts/backup.sh: syntax, argument parsing, dry-run, and rotation logic."""
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = ROOT / "scripts" / "backup.sh"
UPGRADE_SCRIPT = ROOT / "scripts" / "upgrade.sh"


def _run_backup(args: list[str], env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    """Run backup.sh with a controlled environment."""
    env = {**os.environ}
    # Clear DB_* vars so the script reads from the env file, not the system
    # environment (which may have stale values from the developer's setup).
    for key in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"):
        env.pop(key, None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(BACKUP_SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _make_env_file(path: Path, **kwargs) -> Path:
    """Write a minimal myportal.env file."""
    defaults = {
        "DB_HOST": "localhost",
        "DB_PORT": "3306",
        "DB_USER": "testuser",
        "DB_PASSWORD": "testpass",
        "DB_NAME": "testdb",
    }
    defaults.update(kwargs)
    lines = [f"{k}={v}" for k, v in defaults.items()]
    path.write_text("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# Syntax and basic CLI
# ---------------------------------------------------------------------------

def test_backup_script_syntax():
    """The backup script must be valid bash."""
    result = subprocess.run(
        ["bash", "-n", str(BACKUP_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Syntax error: {result.stderr}"


def test_backup_script_help():
    """--help prints usage and exits 0."""
    result = _run_backup(["--help"])
    assert result.returncode == 0
    assert "backup" in result.stdout.lower()
    assert "--label" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--restore" in result.stdout


def test_backup_script_rejects_unknown_option():
    """Unknown options must produce a non-zero exit."""
    result = _run_backup(["--bogus"])
    assert result.returncode != 0
    assert "unknown option" in result.stderr


def test_backup_script_rejects_conflicting_flags():
    """--db-only and --files-only are mutually exclusive."""
    result = _run_backup(["--db-only", "--files-only"])
    assert result.returncode != 0
    assert "mutually exclusive" in result.stderr


def test_backup_script_rejects_restore_with_db_only():
    """--restore cannot be combined with --db-only."""
    result = _run_backup(["--restore", "somefile.sql.gz", "--db-only"])
    assert result.returncode != 0
    assert "cannot be combined" in result.stderr


def test_backup_script_rejects_invalid_retention():
    """MYPORTAL_BACKUPS_TO_KEEP must be a positive integer."""
    result = _run_backup(
        ["--dry-run"],
        env_overrides={"MYPORTAL_BACKUPS_TO_KEEP": "abc"},
    )
    assert result.returncode != 0
    assert "positive integer" in result.stderr


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------

def test_backup_dry_run_creates_no_files(tmp_path):
    """--dry-run must not create the backup directory or any files."""
    env_file = _make_env_file(tmp_path / "myportal.env")
    backup_dir = tmp_path / "backups"
    shared_root = tmp_path / "shared"

    result = _run_backup(
        ["--dry-run", "--label", "test"],
        env_overrides={
            "MYPORTAL_ENV_FILE": str(env_file),
            "BACKUP_DIR": str(backup_dir),
            "MYPORTAL_SHARED_ROOT": str(shared_root),
        },
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "[dry-run]" in output
    # No backup directory should have been created
    assert not backup_dir.exists()


def test_backup_dry_run_reports_configuration(tmp_path):
    """--dry-run output must include the database and file configuration."""
    env_file = _make_env_file(
        tmp_path / "myportal.env",
        DB_HOST="db.example.com",
        DB_PORT="5432",
        DB_NAME="myportal_db",
    )
    result = _run_backup(
        ["--dry-run"],
        env_overrides={
            "MYPORTAL_ENV_FILE": str(env_file),
            "BACKUP_DIR": str(tmp_path / "backups"),
            "MYPORTAL_SHARED_ROOT": str(tmp_path / "shared"),
        },
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "db.example.com" in output
    assert "5432" in output
    assert "myportal_db" in output


# ---------------------------------------------------------------------------
# Rotation logic (dry-run)
# ---------------------------------------------------------------------------

def test_rotation_dry_run_identifies_old_files(tmp_path):
    """With keep=3 and 5 existing db backups, dry-run must flag 2 for deletion."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(mode=0o700)
    shared_root = tmp_path / "shared"
    env_file = _make_env_file(tmp_path / "myportal.env")

    # Create 5 fake db backups with staggered modification times.
    now = time.time()
    for i in range(5):
        ts = now - (5 - i) * 3600  # oldest first
        f = backup_dir / f"db-test-2026010{i + 1}T000000Z.sql.gz"
        f.write_text(f"fake backup {i}")
        os.utime(f, (ts, ts))

    result = _run_backup(
        ["--dry-run", "--db-only", "--label", "test"],
        env_overrides={
            "MYPORTAL_ENV_FILE": str(env_file),
            "BACKUP_DIR": str(backup_dir),
            "MYPORTAL_BACKUPS_TO_KEEP": "3",
            "MYPORTAL_SHARED_ROOT": str(shared_root),
        },
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    # 5 existing - 3 to keep = 2 to delete
    assert output.count("Would delete") == 2


def test_rotation_dry_run_no_deletion_under_limit(tmp_path):
    """With keep=10 and only 3 existing backups, nothing should be deleted."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(mode=0o700)
    shared_root = tmp_path / "shared"
    env_file = _make_env_file(tmp_path / "myportal.env")

    now = time.time()
    for i in range(3):
        ts = now - (3 - i) * 3600
        f = backup_dir / f"db-test-2026010{i + 1}T000000Z.sql.gz"
        f.write_text(f"fake backup {i}")
        os.utime(f, (ts, ts))

    result = _run_backup(
        ["--dry-run", "--db-only", "--label", "test"],
        env_overrides={
            "MYPORTAL_ENV_FILE": str(env_file),
            "BACKUP_DIR": str(backup_dir),
            "MYPORTAL_BACKUPS_TO_KEEP": "10",
            "MYPORTAL_SHARED_ROOT": str(shared_root),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "Would delete" not in result.stderr


# ---------------------------------------------------------------------------
# Upgrade script integration
# ---------------------------------------------------------------------------

def test_upgrade_script_has_pre_upgrade_backup():
    """upgrade.sh must call run_pre_upgrade_backup before run_migration_phase."""
    script = UPGRADE_SCRIPT.read_text()
    assert "run_pre_upgrade_backup" in script
    assert "SKIP_PRE_UPGRADE_BACKUP" in script
    # The backup must be called before the migration phase.
    backup_idx = script.index("run_pre_upgrade_backup || exit 1")
    migration_idx = script.index('run_migration_phase "$RELEASE_DIR"')
    assert backup_idx < migration_idx, (
        "Pre-upgrade backup must run before migrations."
    )


def test_upgrade_script_installs_backup_timer():
    """upgrade.sh must install the backup timer during deployment."""
    script = UPGRADE_SCRIPT.read_text()
    assert "install_backup_timer" in script
    assert "myportal-backup.timer" in script


def test_upgrade_script_installs_backup_command():
    """upgrade.sh must install a stable myportal-backup command."""
    script = UPGRADE_SCRIPT.read_text()
    assert "install_backup_command" in script
    assert "myportal-backup" in script


def test_systemd_units_exist():
    """The systemd unit files must exist in the deploy directory."""
    service = ROOT / "deploy" / "systemd" / "myportal-backup.service"
    timer = ROOT / "deploy" / "systemd" / "myportal-backup.timer"
    assert service.exists(), "myportal-backup.service not found"
    assert timer.exists(), "myportal-backup.timer not found"
    assert "Type=oneshot" in service.read_text()
    assert "OnCalendar=daily" in timer.read_text()
    assert "Persistent=true" in timer.read_text()
