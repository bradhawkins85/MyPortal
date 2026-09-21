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
