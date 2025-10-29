from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_env_example_sets_auto_refresh_false() -> None:
    env_template = PROJECT_ROOT / ".env.example"
    contents = env_template.read_text(encoding="utf-8")
    assert "ENABLE_AUTO_REFRESH=false" in contents


def test_env_example_sets_automation_interval() -> None:
    env_template = PROJECT_ROOT / ".env.example"
    contents = env_template.read_text(encoding="utf-8")
    assert "AUTOMATION_RUNNER_INTERVAL_SECONDS=15" in contents


def test_restart_script_seeds_auto_refresh() -> None:
    script_path = PROJECT_ROOT / "scripts" / "restart.sh"
    contents = script_path.read_text(encoding="utf-8")
    assert 'ensure_env_default "$PYTHON_BIN" "ENABLE_AUTO_REFRESH" "false"' in contents


def test_restart_script_seeds_automation_interval() -> None:
    script_path = PROJECT_ROOT / "scripts" / "restart.sh"
    contents = script_path.read_text(encoding="utf-8")
    assert 'ensure_env_default "$PYTHON_BIN" "AUTOMATION_RUNNER_INTERVAL_SECONDS" "15"' in contents


def test_upgrade_script_seeds_auto_refresh() -> None:
    script_path = PROJECT_ROOT / "scripts" / "upgrade.sh"
    contents = script_path.read_text(encoding="utf-8")
    assert 'ensure_env_default "$PYTHON_INTERPRETER" "ENABLE_AUTO_REFRESH" "false"' in contents


def test_upgrade_script_seeds_automation_interval() -> None:
    script_path = PROJECT_ROOT / "scripts" / "upgrade.sh"
    contents = script_path.read_text(encoding="utf-8")
    assert 'ensure_env_default "$PYTHON_INTERPRETER" "AUTOMATION_RUNNER_INTERVAL_SECONDS" "15"' in contents


def test_install_environment_seeds_auto_refresh() -> None:
    script_path = PROJECT_ROOT / "scripts" / "install_environment.sh"
    contents = script_path.read_text(encoding="utf-8")
    assert 'ensure_env_default "ENABLE_AUTO_REFRESH" "false"' in contents


def test_install_environment_seeds_automation_interval() -> None:
    script_path = PROJECT_ROOT / "scripts" / "install_environment.sh"
    contents = script_path.read_text(encoding="utf-8")
    assert 'ensure_env_default "AUTOMATION_RUNNER_INTERVAL_SECONDS" "15"' in contents
