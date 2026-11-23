from pathlib import Path


MIGRATION = Path(__file__).parent.parent / "migrations" / "572_rmm_windows_activation.sql"


def test_rmm_windows_activation_migration_adds_the_summary_column():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.startswith("-- phase: expand")
    assert (
        "ALTER TABLE rmm_device_inventory ADD COLUMN IF NOT EXISTS windows_activation VARCHAR(32) NULL" in sql
    )
