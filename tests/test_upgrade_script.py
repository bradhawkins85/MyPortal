from pathlib import Path


def test_upgrade_script_prefers_project_virtualenv():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.sh"
    contents = script_path.read_text()
    assert 'VENV_DIR="${PROJECT_ROOT}/.venv"' in contents
    assert '"${VENV_DIR}/bin/python"' in contents
    assert '"${VENV_DIR}/Scripts/python.exe"' in contents
    assert 'command -v python3' in contents and 'command -v python' in contents
    assert 'Run scripts/restart.sh to reinstall dependencies and restart the service.' in contents
    assert 'FORCE_RESTART' in contents
    assert 'running restart helper' in contents
