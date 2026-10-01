from pathlib import Path


def test_process_update_flag_script_uses_system_update_flag_and_upgrade_helper():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "process_update_flag.sh"
    contents = script_path.read_text()
    assert "system_update.flag" in contents
    assert "upgrade.sh" in contents
    assert "--graceful" in contents
    assert "APP_UPGRADE_MODE" in contents
    assert "requested_mode" in contents
    assert '"$update_id" running' in contents
    assert '"$update_id" succeeded' in contents
    assert '"$update_id" failed' in contents
    assert "system_update_report.py" in contents
    assert "flock" in contents
    assert "validate_flag_file()" in contents
    assert "Refusing to process symlinked update flag" in contents


def test_process_update_flag_keeps_root_artifacts_out_of_service_writable_state():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "process_update_flag.sh"
    contents = script_path.read_text()
    # Lock and captured output live in a root-only directory.
    assert 'PRIVATE_DIR="/var/lib/myportal-updater"' in contents
    assert 'LOCK_FILE="${PRIVATE_DIR}/system_update.lock"' in contents
    assert 'mktemp "${PRIVATE_DIR}/system-update-output.XXXXXX"' in contents
    assert "${FLAG_DIR}/system-update-output" not in contents
    # The flag is read once without following symlinks.
    assert "iflag=nofollow" in contents
    # History is reported as the service account, with output on stdin.
    assert 'runuser -u "$SERVICE_USER" -- env' in contents
    assert '--output-file "$output_file"' not in contents
    assert '--output-file - <"$output_file"' in contents
