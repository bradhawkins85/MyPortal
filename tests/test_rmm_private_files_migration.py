from pathlib import Path


MIGRATION = Path(__file__).parent.parent / "migrations" / "571_rmm_private_files.sql"


def test_rmm_private_files_migration_stores_the_synced_file_list():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.startswith("-- phase: expand")
    assert "CREATE TABLE IF NOT EXISTS rmm_private_files" in sql
    for column in ("path VARCHAR(1024) NOT NULL", "sha VARCHAR(64) NOT NULL", "size BIGINT", "company_id INT NULL"):
        assert column in sql
