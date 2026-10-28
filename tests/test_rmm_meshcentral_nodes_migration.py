from pathlib import Path


MIGRATION = Path(__file__).parent.parent / "migrations" / "531_rmm_meshcentral_nodes.sql"


def test_rmm_meshcentral_nodes_migration_keeps_one_node_per_asset():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.startswith("-- phase: expand")
    assert "CREATE TABLE IF NOT EXISTS rmm_meshcentral_nodes" in sql
    assert "asset_id INT NOT NULL PRIMARY KEY" in sql
    assert "REFERENCES assets(id) ON DELETE CASCADE" in sql
