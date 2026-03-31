from pathlib import Path


def test_upgrade_script_prefers_project_virtualenv():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh"
    contents = script_path.read_text()
    assert 'VENV_DIR="${PROJECT_ROOT}/.venv"' in contents
    assert '"${VENV_DIR}/bin/python"' in contents
    assert '"${VENV_DIR}/Scripts/python.exe"' in contents
    assert 'command -v python3' in contents and 'command -v python' in contents
    assert 'RESTART_FLAG_FILE' in contents
    assert 'Flagged pending dependency install and service restart' in contents
    assert 'FORCE_RESTART' in contents
    assert 'FORCE_RESTART="$(read_env_var "FORCE_RESTART" "0")"' in contents
    assert 'flagging dependency install and service restart' in contents


def test_upgrade_script_installs_dependencies_on_update():
    """Verify that upgrade.sh installs dependencies when code changes are detected."""
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh"
    contents = script_path.read_text()

    # An install_dependencies function must exist and invoke pip install.
    assert "install_dependencies()" in contents
    assert 'pip install --upgrade "$PROJECT_ROOT"' in contents

    # The function must be called in the code-change branch so that both
    # the normal and --auto-fallback paths receive updated dependencies.
    lines = contents.splitlines()
    in_change_block = False
    install_after_update = False
    for line in lines:
        stripped = line.strip()
        if 'PRE_PULL_HEAD' in stripped and 'POST_PULL_HEAD' in stripped:
            in_change_block = True
        if in_change_block and stripped == "install_dependencies":
            install_after_update = True
            break
    assert install_after_update, (
        "install_dependencies must be called when the repository is updated"
    )


def test_upgrade_script_installs_dependencies_on_force_restart():
    """Verify that upgrade.sh installs dependencies on FORCE_RESTART=1."""
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh"
    contents = script_path.read_text()

    lines = contents.splitlines()
    in_force_block = False
    install_in_force = False
    for line in lines:
        stripped = line.strip()
        if 'FORCE_RESTART' in stripped and '"1"' in stripped and 'elif' in stripped:
            in_force_block = True
        if in_force_block and stripped == "install_dependencies":
            install_in_force = True
            break
    assert install_in_force, (
        "install_dependencies must be called when FORCE_RESTART is set"
    )
