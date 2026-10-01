from pathlib import Path
import json

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


def test_released_migration_uses_companion_metadata_without_sql_change(tmp_path):
    path = _migration(tmp_path, "380_released.sql", "ALTER TABLE example ADD value INT;\n")
    (tmp_path / "deployment_metadata.json").write_text(
        json.dumps({"schema_version": 1, "migrations": {path.name: {
            "phase": "contract", "compatible-from": "*", "compatible-to": "*",
            "maintenance": True,
        }}}),
        encoding="utf-8",
    )

    metadata = Database()._migration_metadata(path)

    assert metadata == {
        "phase": "contract", "legacy": True, "compatible_from": "*",
        "compatible_to": "*", "maintenance": True,
    }


def test_supported_upgrade_window_has_explicit_deployment_metadata():
    migrations_dir = Path(__file__).parent.parent / "migrations"
    manifest = json.loads(
        (migrations_dir / "deployment_metadata.json").read_text(encoding="utf-8")
    )["migrations"]
    expected = {
        path.name
        for path in migrations_dir.glob("*.sql")
        if 376 <= int(path.name.split("_", 1)[0]) <= 382
    }

    assert set(manifest) == expected
    for name in expected:
        metadata = Database()._migration_metadata(migrations_dir / name)
        assert metadata["legacy"] is True
        assert metadata["compatible_from"] == "*"
        assert metadata["compatible_to"] == "*"


def test_current_migrations_have_inline_metadata():
    migrations_dir = Path(__file__).parent.parent / "migrations"
    for path in migrations_dir.glob("*.sql"):
        if int(path.name.split("_", 1)[0]) > 382:
            assert "-- phase:" in "\n".join(
                path.read_text(encoding="utf-8").splitlines()[:5]
            ), f"{path.name} is missing inline deployment metadata"
