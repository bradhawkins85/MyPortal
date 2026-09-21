from pathlib import Path

import pytest

from app.core.database import Database


def _migration(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_expand_metadata_supports_rolling_compatibility(tmp_path):
    database = Database()
    path = _migration(
        tmp_path,
        "900_expand.sql",
        "-- phase: expand\n-- compatible-from: old\n-- compatible-to: new\nSELECT 1;\n",
    )
    metadata = database._migration_metadata(path)
    database._validate_migration_compatibility(path, metadata, "old", "new", False)


def test_contract_without_compatibility_is_blocked(tmp_path):
    database = Database()
    path = _migration(tmp_path, "900_contract.sql", "-- phase: contract\nDROP TABLE old;\n")
    metadata = database._migration_metadata(path)
    with pytest.raises(RuntimeError, match="requires compatibility metadata"):
        database._validate_migration_compatibility(path, metadata, "old", "new", False)


def test_maintenance_migration_is_blocked_outside_upg01(tmp_path):
    database = Database()
    path = _migration(
        tmp_path,
        "900_contract.sql",
        "-- phase: contract\n-- maintenance: true\nDROP TABLE old;\n",
    )
    metadata = database._migration_metadata(path)
    with pytest.raises(RuntimeError, match="UPG01"):
        database._validate_migration_compatibility(path, metadata, "old", "new", False)


def test_new_migration_requires_phase(tmp_path):
    database = Database()
    path = _migration(tmp_path, "schema_change.sql", "SELECT 1;\n")
    with pytest.raises(RuntimeError, match="phase metadata"):
        database._migration_metadata(path)
