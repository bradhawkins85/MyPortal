from pathlib import Path


def test_process_update_flag_script_mentions_restart_helper():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "process_update_flag.sh"
    contents = script_path.read_text()
    assert "restart_required.flag" in contents
    assert "restart.sh" in contents
    assert "flock" in contents
