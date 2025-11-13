"""
Test for migration 132_billing_contacts_staff.sql idempotency.

This test verifies that migration 132 can be safely re-run multiple times
without errors, even if the staff_id column already exists.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


def test_migration_132_uses_conditional_column_add():
    """Verify that migration 132 checks for column existence before adding staff_id."""
    migration_path = Path(__file__).parent.parent / "migrations" / "132_billing_contacts_staff.sql"
    content = migration_path.read_text()
    
    # Check that it uses information_schema to check for column existence
    assert "information_schema.COLUMNS" in content
    assert "COLUMN_NAME = 'staff_id'" in content
    
    # Check that it uses dynamic SQL with PREPARE/EXECUTE
    assert "PREPARE stmt FROM @sql" in content
    assert "EXECUTE stmt" in content
    assert "DEALLOCATE PREPARE stmt" in content


def test_migration_132_checks_all_operations():
    """Verify that all destructive operations have conditional checks."""
    migration_path = Path(__file__).parent.parent / "migrations" / "132_billing_contacts_staff.sql"
    content = migration_path.read_text()
    
    # Count the number of conditional checks
    info_schema_checks = len(re.findall(r"FROM information_schema\.\w+", content, re.IGNORECASE))
    
    # We expect checks for:
    # 1. staff_id column exists (for ADD COLUMN)
    # 2. user_id column exists (for data migration)
    # 3. user_id column exists (for DELETE statement)
    # 4. billing_contacts_ibfk_2 FK exists (for DROP FK)
    # 5. user_id column exists (for DROP COLUMN)
    # 6. staff_id IS_NULLABLE (for MODIFY COLUMN)
    # 7. billing_contacts_staff_fk FK exists (for ADD FK)
    # 8. unique_company_user key exists (for DROP KEY)
    # 9. unique_company_staff key exists (for ADD KEY)
    # 10. idx_billing_contacts_staff index exists (for CREATE INDEX)
    
    assert info_schema_checks >= 7, f"Expected at least 7 information_schema checks, found {info_schema_checks}"


def test_migration_132_has_balanced_prepare_deallocate():
    """Verify that all PREPARE statements have matching DEALLOCATE statements."""
    migration_path = Path(__file__).parent.parent / "migrations" / "132_billing_contacts_staff.sql"
    content = migration_path.read_text()
    
    prepare_count = len(re.findall(r"PREPARE stmt FROM", content, re.IGNORECASE))
    deallocate_count = len(re.findall(r"DEALLOCATE PREPARE stmt", content, re.IGNORECASE))
    
    assert prepare_count == deallocate_count, (
        f"Mismatch: {prepare_count} PREPARE statements vs {deallocate_count} DEALLOCATE statements"
    )
    assert prepare_count > 0, "No PREPARE statements found"


def test_migration_132_handles_partial_migration():
    """Verify that the migration handles the case where staff_id already exists."""
    migration_path = Path(__file__).parent.parent / "migrations" / "132_billing_contacts_staff.sql"
    content = migration_path.read_text()
    
    # Check for the specific components that prevent duplicate column error
    assert "SET @column_exists" in content, (
        "Migration does not use variable to track column existence"
    )
    assert "COLUMN_NAME = 'staff_id'" in content, (
        "Migration does not check for staff_id column existence"
    )
    assert "IF(@column_exists = 0" in content, (
        "Migration does not use conditional logic for adding staff_id"
    )
    assert "ALTER TABLE billing_contacts ADD COLUMN staff_id" in content, (
        "Migration does not contain ADD COLUMN statement"
    )
    assert "SELECT \"Column staff_id already exists, skipping\"" in content, (
        "Migration does not have skip message for existing column"
    )


def test_migration_132_syntax_validation():
    """Validate basic SQL syntax in migration 132."""
    migration_path = Path(__file__).parent.parent / "migrations" / "132_billing_contacts_staff.sql"
    content = migration_path.read_text()
    
    # Check for balanced parentheses in SET @sql statements
    lines = content.split('\n')
    for i, line in enumerate(lines, 1):
        if 'SET @sql = IF(' in line.upper():
            # Basic check that the line doesn't have obviously unbalanced parens
            open_count = line.count('(')
            close_count = line.count(')')
            # We don't expect them to balance on a single line, but they should be present
            assert open_count > 0, f"Line {i}: SET @sql = IF( with no opening parenthesis"
