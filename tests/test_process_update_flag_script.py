from pathlib import Path


def test_process_update_flag_script_uses_system_update_flag_and_upgrade_helper():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "process_update_flag.sh"
    contents = script_path.read_text()
    assert "system_update.flag" in contents
    assert "upgrade.sh" in contents
    assert "flock" in contents
