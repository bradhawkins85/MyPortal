from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_SOURCE = (ROOT / "app/main.py").read_text(encoding="utf-8")


def _startup_source() -> str:
    start = MAIN_SOURCE.index("async def on_startup() -> None:")
    end = MAIN_SOURCE.index("\n@app.on_event(\"shutdown\")", start)
    return MAIN_SOURCE[start:end]


def test_lifespan_startup_does_not_wait_for_external_update_checks():
    startup = _startup_source()

    assert "run_system_update" not in startup
    assert "fetch_latest_tray_installers" not in startup


def test_lifespan_startup_logs_blocking_local_phases():
    startup = _startup_source()

    assert 'phase="database_connect"' in startup
    assert 'phase="local_bootstrap_tasks"' in startup
