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


def test_process_update_flag_timestamps_every_output_line():
    import re
    import subprocess

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "process_update_flag.sh"
    contents = script_path.read_text()
    # stdout and stderr share one pipeline, so every line is stamped and the
    # log is complete before the result is reported.
    assert '2>&1 | timestamp_lines | tee -a "$output_file"' in contents
    assert "status=${PIPESTATUS[0]}" in contents
    assert "Picked up by the update coordinator" in contents

    function = re.search(r"^timestamp_lines\(\) \{.*?^\}", contents, re.S | re.M).group(0)
    result = subprocess.run(
        ["bash", "-c", function + "\nprintf 'one\\ntwo' | timestamp_lines"],
        text=True, capture_output=True, check=True, env={"TZ": "Australia/Sydney", "PATH": "/usr/bin:/bin"},
    )
    lines = result.stdout.splitlines()
    assert [re.sub(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] ", "", line) for line in lines] == ["one", "two"]
