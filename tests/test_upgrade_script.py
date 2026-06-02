from pathlib import Path


def test_upgrade_script_prefers_project_virtualenv():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh"
    contents = script_path.read_text()
    assert 'VENV_DIR="${PROJECT_ROOT}/.venv"' in contents
    assert '"${VENV_DIR}/bin/python"' in contents
    assert '"${VENV_DIR}/Scripts/python.exe"' in contents
    assert 'command -v python3' in contents and 'command -v python' in contents


def test_upgrade_script_supports_explicit_modes_and_env_default():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh"
    contents = script_path.read_text()

    assert "--graceful" in contents
    assert "--rolling" in contents
    assert "--restart" in contents
    assert "APP_UPGRADE_MODE" in contents
    assert "resolve_requested_upgrade_mode()" in contents
    assert "resolve_effective_upgrade_mode()" in contents


def test_upgrade_script_records_upgrade_status():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh"
    contents = script_path.read_text()

    assert "system_update.status" in contents
    assert "write_upgrade_status()" in contents
    assert "ready_wait_seconds=" in contents


def test_upgrade_script_installs_dependencies_on_update():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh"
    contents = script_path.read_text()

    assert "install_dependencies()" in contents
    assert 'pip install --upgrade "$PROJECT_ROOT"' in contents

    lines = contents.splitlines()
    in_change_block = False
    install_after_update = False
    for line in lines:
        stripped = line.strip()
        if 'if [[ "$PRE_PULL_HEAD" != "$POST_PULL_HEAD" ]]; then' in stripped:
            in_change_block = True
        if in_change_block and stripped == "install_dependencies":
            install_after_update = True
            break
    assert install_after_update


def test_upgrade_script_installs_dependencies_on_force_restart():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh"
    contents = script_path.read_text()

    lines = contents.splitlines()
    in_force_block = False
    install_in_force = False
    for line in lines:
        stripped = line.strip()
        if 'elif [[ "$FORCE_RESTART" == "1" ]]; then' in stripped:
            in_force_block = True
        if in_force_block and stripped == "install_dependencies":
            install_in_force = True
            break
    assert install_in_force


def test_upgrade_script_skips_restart_for_feature_pack_only_diff():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh"
    contents = script_path.read_text()

    assert "FEATURE_PACK_DIFF_SLUGS" in contents
    assert "git diff --name-only" in contents
    assert "app/features/" in contents
    assert "feature_pack_reload.flag" in contents
    assert "Feature pack hot-reload scheduled" in contents
    assert 'if [[ "$FORCE_RESTART" != "1" ]]; then' in contents


def test_upgrade_script_promotes_sensitive_changes_to_restart():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh"
    contents = script_path.read_text()

    assert "dependency_manifest_changed|destructive_migration_phase" in contents
    assert "printf '%s' \"restart\"" in contents
