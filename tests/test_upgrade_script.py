import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/upgrade.sh").read_text()


def test_upgrade_prepares_revision_without_mutating_control_checkout():
    assert 'git fetch --quiet origin main' in SCRIPT
    assert 'git archive "$revision"' in SCRIPT
    assert 'RELEASE_DIR="${RELEASE_ROOT}/${TARGET_REVISION}"' in SCRIPT
    assert "git pull" not in SCRIPT
    assert "git restore" not in SCRIPT
    assert "chown -R" not in SCRIPT


def test_release_has_private_dependencies_and_shared_mutable_state():
    assert 'python3 -m venv "${release}/.venv"' in SCRIPT
    assert 'ln -s "$SHARED_ROOT" "$staging/var"' in SCRIPT
    assert 'find "$staging" -type f -exec chmod a-w' in SCRIPT
    assert 'ln -s "$ENV_FILE" "$staging/.env"' in SCRIPT


def _run_configuration_validation(tmp_path: Path, contents: str, **environment: str):
    env_file = tmp_path / ".env"
    env_file.write_text(contents)
    function = SCRIPT[SCRIPT.index("validate_required_configuration() {") : SCRIPT.index("\natomic_link()")]
    child_environment = dict(os.environ)
    child_environment.pop("SESSION_SECRET", None)
    child_environment.pop("TOTP_ENCRYPTION_KEY", None)
    return subprocess.run(
        [
            "bash",
            "-c",
            'set -Eeuo pipefail\nENV_FILE="$MYPORTAL_ENV_FILE"\n'
            + function
            + "\nvalidate_required_configuration",
        ],
        text=True,
        capture_output=True,
        env={**child_environment, "MYPORTAL_ENV_FILE": str(env_file), **environment},
        check=False,
    )


def test_upgrade_accepts_required_secrets_from_existing_env_file(tmp_path):
    result = _run_configuration_validation(
        tmp_path,
        'SESSION_SECRET="existing session secret"\nTOTP_ENCRYPTION_KEY=existing-totp-key\n',
    )

    assert result.returncode == 0
    assert result.stderr == ""


def test_upgrade_rejects_missing_secrets_before_installing_release(tmp_path):
    result = _run_configuration_validation(tmp_path, "SESSION_SECRET=configured\n")

    assert result.returncode != 0
    assert "Missing required application configuration: TOTP_ENCRYPTION_KEY." in result.stderr
    assert "before running the upgrade" in result.stderr
    validation = SCRIPT.index("validate_required_configuration\n", SCRIPT.index("command -v git"))
    preparation = SCRIPT.index('prepare_release "$TARGET_REVISION"')
    assert validation < preparation


def test_cutover_checks_expected_version_before_nginx_switch():
    version_check = SCRIPT.index('wait_for_version "$(instance_port "$inactive")" "$revision"')
    smoke = SCRIPT.index('smoke_test "$(instance_port "$inactive")" "$revision"')
    cutover = SCRIPT.index('write_upstream "$inactive" "$active"')
    assert version_check < smoke < cutover
    assert "nginx -t" in SCRIPT


def test_failure_rolls_back_links_and_upstream():
    assert "rollback()" in SCRIPT
    assert 'trap \'rollback "$active" "$inactive" "$old_inactive"\' ERR' in SCRIPT
    assert 'write_upstream "$old_active" "$new_instance"' in SCRIPT
    assert 'atomic_link "$PREVIOUS_RELEASE" "$CURRENT_LINK"' in SCRIPT


def test_drain_stops_new_work_before_waiting_for_inflight_requests():
    cutover = SCRIPT.index('write_upstream "$inactive" "$active"')
    drain = SCRIPT.index('sleep "$DRAIN_SECONDS"', cutover)
    current = SCRIPT.index('atomic_link "$release" "$CURRENT_LINK"', drain)
    assert cutover < drain < current


def test_remote_validation_rejects_untrusted_and_embedded_credentials():
    assert "validate_origin_remote()" in SCRIPT
    assert "https://github.com/*|git@github.com:*|ssh://git@github.com/*" in SCRIPT
    assert "credential-bearing HTTPS remotes" in SCRIPT
