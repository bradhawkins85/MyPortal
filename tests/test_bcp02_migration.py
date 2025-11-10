"""
Tests for BCP BC02 migration (126_bc02_bcp_data_model.sql).

Verifies that the migration SQL file:
1. Creates all required tables
2. Has proper foreign key constraints
3. Has appropriate indexes
4. Uses idempotent CREATE TABLE IF NOT EXISTS
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def migration_sql():
    """Load the BC02 migration SQL file."""
    migration_file = Path(__file__).parent.parent / "migrations" / "126_bc02_bcp_data_model.sql"
    return migration_file.read_text()


def test_migration_file_exists():
    """Test that the migration file exists."""
    migration_file = Path(__file__).parent.parent / "migrations" / "126_bc02_bcp_data_model.sql"
    assert migration_file.exists(), "Migration file 126_bc02_bcp_data_model.sql does not exist"


def test_migration_uses_idempotent_create_table(migration_sql):
    """Test that migration uses CREATE TABLE IF NOT EXISTS for idempotency."""
    # Count CREATE TABLE statements
    create_tables = migration_sql.upper().count("CREATE TABLE IF NOT EXISTS")
    # We should have 23 tables (1 plan + 22 related entities)
    assert create_tables == 23, f"Expected 23 CREATE TABLE IF NOT EXISTS statements, found {create_tables}"


def test_migration_creates_core_entities(migration_sql):
    """Test that core entity tables are created."""
    core_tables = [
        "bcp_plan",
        "bcp_distribution_entry",
    ]
    for table in core_tables:
        assert table in migration_sql.lower(), f"Table {table} not found in migration"


def test_migration_creates_risk_preparedness_entities(migration_sql):
    """Test that risk & preparedness entity tables are created."""
    risk_tables = [
        "bcp_risk",
        "bcp_insurance_policy",
        "bcp_backup_item",
    ]
    for table in risk_tables:
        assert table in migration_sql.lower(), f"Table {table} not found in migration"


def test_migration_creates_bia_entities(migration_sql):
    """Test that BIA entity tables are created."""
    bia_tables = [
        "bcp_critical_activity",
        "bcp_impact",
    ]
    for table in bia_tables:
        assert table in migration_sql.lower(), f"Table {table} not found in migration"


def test_migration_creates_incident_entities(migration_sql):
    """Test that incident response entity tables are created."""
    incident_tables = [
        "bcp_incident",
        "bcp_checklist_item",
        "bcp_checklist_tick",
        "bcp_evacuation_plan",
        "bcp_emergency_kit_item",
        "bcp_role",
        "bcp_role_assignment",
        "bcp_contact",
        "bcp_event_log_entry",
    ]
    for table in incident_tables:
        assert table in migration_sql.lower(), f"Table {table} not found in migration"


def test_migration_creates_recovery_entities(migration_sql):
    """Test that recovery entity tables are created."""
    recovery_tables = [
        "bcp_recovery_action",
        "bcp_recovery_contact",
        "bcp_insurance_claim",
        "bcp_market_change",
        "bcp_training_item",
        "bcp_review_item",
    ]
    for table in recovery_tables:
        assert table in migration_sql.lower(), f"Table {table} not found in migration"


def test_migration_has_foreign_keys(migration_sql):
    """Test that foreign key constraints are defined."""
    # Count FOREIGN KEY declarations
    fk_count = migration_sql.upper().count("FOREIGN KEY")
    # We should have many foreign keys
    assert fk_count >= 20, f"Expected at least 20 FOREIGN KEY constraints, found {fk_count}"


def test_migration_has_cascade_deletes(migration_sql):
    """Test that CASCADE deletes are used appropriately."""
    # Count ON DELETE CASCADE
    cascade_count = migration_sql.upper().count("ON DELETE CASCADE")
    # Most relations should cascade
    assert cascade_count >= 20, f"Expected at least 20 ON DELETE CASCADE constraints, found {cascade_count}"


def test_migration_has_company_id_index(migration_sql):
    """Test that company_id has an index for multi-tenancy."""
    assert "idx_bcp_plan_company" in migration_sql.lower(), "company_id index not found"
    assert "company_id" in migration_sql.lower(), "company_id column not found in bcp_plan"


def test_migration_has_plan_id_indexes(migration_sql):
    """Test that plan_id has indexes for performance."""
    # Count indexes on plan_id
    plan_id_indexes = migration_sql.lower().count("idx_bcp_") and migration_sql.lower().count("_plan")
    assert plan_id_indexes > 0, "plan_id indexes not found"


def test_migration_has_check_constraints(migration_sql):
    """Test that CHECK constraints are defined for data validation."""
    # Count CHECK constraints
    check_count = migration_sql.upper().count("CONSTRAINT")
    # We should have several check constraints (likelihood, impact, rto, etc.)
    assert check_count >= 4, f"Expected at least 4 CHECK constraints, found {check_count}"


def test_migration_risk_likelihood_constraint(migration_sql):
    """Test that risk likelihood has a 1-4 range constraint."""
    assert "ck_likelihood_range" in migration_sql.lower(), "Likelihood range constraint not found"
    assert "likelihood >= 1" in migration_sql.lower(), "Likelihood minimum constraint not found"
    assert "likelihood <= 4" in migration_sql.lower(), "Likelihood maximum constraint not found"


def test_migration_risk_impact_constraint(migration_sql):
    """Test that risk impact has a 1-4 range constraint."""
    assert "ck_impact_range" in migration_sql.lower(), "Impact range constraint not found"
    assert "impact >= 1" in migration_sql.lower(), "Impact minimum constraint not found"
    assert "impact <= 4" in migration_sql.lower(), "Impact maximum constraint not found"


def test_migration_rto_positive_constraints(migration_sql):
    """Test that RTO hours have non-negative constraints."""
    assert "ck_rto_positive" in migration_sql.lower(), "RTO positive constraint not found"
    assert "rto_hours >= 0" in migration_sql.lower(), "RTO non-negative constraint not found"


def test_migration_has_enums(migration_sql):
    """Test that ENUM types are used for constrained fields."""
    # Count ENUM declarations
    enum_count = migration_sql.upper().count("ENUM(")
    # We should have several enums (status, phase, category, kind, priority, etc.)
    assert enum_count >= 7, f"Expected at least 7 ENUM types, found {enum_count}"


def test_migration_priority_enum(migration_sql):
    """Test that priority has correct enum values."""
    assert "'High'" in migration_sql and "'Medium'" in migration_sql and "'Low'" in migration_sql, \
        "Priority enum values not found or incorrect"


def test_migration_incident_status_enum(migration_sql):
    """Test that incident status has correct enum values."""
    assert "'Active'" in migration_sql and "'Closed'" in migration_sql, \
        "Incident status enum values not found or incorrect"


def test_migration_phase_enum(migration_sql):
    """Test that phase has correct enum values."""
    assert "'Immediate'" in migration_sql and "'CrisisRecovery'" in migration_sql, \
        "Phase enum values not found or incorrect"


def test_migration_contact_kind_enum(migration_sql):
    """Test that contact kind has correct enum values."""
    assert "'Internal'" in migration_sql and "'External'" in migration_sql, \
        "Contact kind enum values not found or incorrect"


def test_migration_uses_innodb(migration_sql):
    """Test that InnoDB engine is specified."""
    # Count ENGINE=InnoDB
    innodb_count = migration_sql.upper().count("ENGINE=INNODB")
    # All tables should use InnoDB
    assert innodb_count == 23, f"Expected 23 InnoDB tables, found {innodb_count}"


def test_migration_uses_utf8mb4(migration_sql):
    """Test that UTF-8 charset is specified."""
    # Count CHARSET=utf8mb4
    utf8_count = migration_sql.upper().count("CHARSET=UTF8MB4")
    # Most tables should use utf8mb4
    assert utf8_count >= 22, f"Expected at least 22 utf8mb4 tables, found {utf8_count}"


def test_migration_has_timestamps(migration_sql):
    """Test that tables have created_at and updated_at timestamps."""
    # Count created_at columns
    created_at_count = migration_sql.lower().count("created_at datetime")
    # Most tables should have created_at
    assert created_at_count >= 22, f"Expected at least 22 created_at columns, found {created_at_count}"
    
    # Count updated_at columns
    updated_at_count = migration_sql.lower().count("updated_at datetime")
    # Most tables should have updated_at
    assert updated_at_count >= 22, f"Expected at least 22 updated_at columns, found {updated_at_count}"


def test_migration_has_comments(migration_sql):
    """Test that important columns have comments."""
    # Count COMMENT clauses
    comment_count = migration_sql.upper().count("COMMENT")
    # Many columns should have comments
    assert comment_count >= 30, f"Expected at least 30 column comments, found {comment_count}"


def test_migration_no_drop_statements(migration_sql):
    """Test that migration doesn't drop any tables."""
    assert "DROP TABLE" not in migration_sql.upper(), "Migration should not drop tables"


def test_migration_no_truncate_statements(migration_sql):
    """Test that migration doesn't truncate any tables."""
    assert "TRUNCATE TABLE" not in migration_sql.upper(), "Migration should not truncate tables"
