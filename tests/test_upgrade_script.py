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
