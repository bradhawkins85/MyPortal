from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_env_example_sets_auto_refresh_false() -> None:
    env_template = PROJECT_ROOT / ".env.example"
    contents = env_template.read_text(encoding="utf-8")
    assert "ENABLE_AUTO_REFRESH=false" in contents


def test_env_example_does_not_expose_feature_pack_toggle() -> None:
    env_template = PROJECT_ROOT / ".env.example"
    lines = env_template.read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith("FEATURE_PACKS=") for line in lines)


def test_env_example_has_empty_component_exclusion_defaults() -> None:
    contents = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DISABLED_FEATURE_PACKS=\n" in contents
    assert "DISABLED_MODULES=\n" in contents


def test_restart_script_seeds_auto_refresh() -> None:
    script_path = PROJECT_ROOT / "scripts" / "restart.sh"
    contents = script_path.read_text(encoding="utf-8")
    assert 'ensure_env_default "$PYTHON_BIN" "ENABLE_AUTO_REFRESH" "false"' in contents
    assert 'ensure_env_default "$PYTHON_BIN" "DISABLED_FEATURE_PACKS" ""' in contents
    assert 'ensure_env_default "$PYTHON_BIN" "DISABLED_MODULES" ""' in contents


def test_upgrade_script_preserves_persistent_environment() -> None:
    script_path = PROJECT_ROOT / "scripts" / "upgrade.sh"
    contents = script_path.read_text(encoding="utf-8")
    assert 'ln -s "$ENV_FILE" "$staging/.env"' in contents
    assert 'ln -sfn "$ENV_FILE" "$release/.env"' in contents


def test_install_environment_seeds_auto_refresh() -> None:
    script_path = PROJECT_ROOT / "scripts" / "install_environment.sh"
    contents = script_path.read_text(encoding="utf-8")
    assert 'ensure_env_default "ENABLE_AUTO_REFRESH" "false"' in contents
    assert 'ensure_env_default "DISABLED_FEATURE_PACKS" ""' in contents
    assert 'ensure_env_default "DISABLED_MODULES" ""' in contents
