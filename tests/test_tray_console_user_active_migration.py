from pathlib import Path

MIGRATION = Path(__file__).parent.parent / "migrations" / "554_tray_devices_console_user_active.sql"


def test_migration_adds_console_user_active_column():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "-- phase: expand" in sql
    assert "ALTER TABLE tray_devices ADD COLUMN IF NOT EXISTS console_user_active TINYINT(1) NULL" in sql
