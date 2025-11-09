"""Test subscription_change_requests migration SQL syntax."""
from __future__ import annotations

import re
from pathlib import Path


def test_subscription_change_requests_migration_foreign_key_consistency():
    """Verify that requested_by column definition matches its foreign key constraint."""
    # Read the migration file
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    migration_file = migrations_dir / "116_subscription_change_requests.sql"
    
    assert migration_file.exists(), f"Migration file not found: {migration_file}"
    
    content = migration_file.read_text(encoding="utf-8")
    
    # Extract the requested_by column definition
    # Pattern: "requested_by" followed by data type (INT or INT NULL) and optional NOT NULL
    col_pattern = r'requested_by\s+(INT\s+(?:NOT\s+)?NULL|INT)'
    col_match = re.search(col_pattern, content, re.IGNORECASE)
    
    assert col_match, "Could not find requested_by column definition"
    
    col_def = col_match.group(1).upper().strip()
    
    # Extract the foreign key constraint for requested_by
    # Pattern: FOREIGN KEY (requested_by) ... ON DELETE action
    fk_pattern = r'FOREIGN\s+KEY\s*\(requested_by\)\s+REFERENCES\s+users\(id\)\s+ON\s+DELETE\s+(\w+(?:\s+\w+)?)'
    fk_match = re.search(fk_pattern, content, re.IGNORECASE)
    
    assert fk_match, "Could not find foreign key constraint for requested_by"
    
    fk_action = fk_match.group(1).upper().strip()
    
    # Validate consistency
    is_nullable = "NOT NULL" not in col_def or col_def.endswith("NULL")
    is_set_null = fk_action == "SET NULL"
    
    # If foreign key uses ON DELETE SET NULL, the column must be nullable
    if is_set_null:
        assert is_nullable, (
            f"Invalid foreign key constraint: requested_by column is defined as '{col_def}' "
            f"but foreign key uses 'ON DELETE SET NULL'. "
            f"A NOT NULL column cannot use ON DELETE SET NULL. "
            f"Change column to 'INT NULL' or change foreign key action to 'ON DELETE CASCADE' or 'ON DELETE RESTRICT'."
        )
    
    # If column is NOT NULL, foreign key cannot use SET NULL
    if not is_nullable:
        assert not is_set_null, (
            f"Invalid foreign key constraint: requested_by column is NOT NULL "
            f"but foreign key uses 'ON DELETE SET NULL'. "
            f"Change column to 'INT NULL' or change foreign key action to 'ON DELETE CASCADE' or 'ON DELETE RESTRICT'."
        )


def test_subscription_change_requests_migration_has_proper_indexes():
    """Verify that the migration creates the expected indexes."""
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    migration_file = migrations_dir / "116_subscription_change_requests.sql"
    
    content = migration_file.read_text(encoding="utf-8")
    
    # Check for expected indexes
    expected_indexes = [
        "idx_subscription_change_requests_subscription",
        "idx_subscription_change_requests_status",
        "idx_subscription_change_requests_requested_at",
    ]
    
    for index_name in expected_indexes:
        assert index_name in content, f"Expected index '{index_name}' not found in migration"
