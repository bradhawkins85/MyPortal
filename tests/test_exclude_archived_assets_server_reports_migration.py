"""Regression coverage for excluding archived assets from server reports."""

import sqlite3
from pathlib import Path

MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "537_exclude_archived_assets_from_server_reports.sql"
)

ARCHIVE_FILTER = (
    "NOT EXISTS (SELECT 1 FROM asset_custom_field_values v "
    "JOIN asset_custom_field_definitions d ON d.id = v.field_definition_id "
    "WHERE v.asset_id = a.id AND LOWER(d.name) IN (''archive asset'', ''archive_asset'') "
    "AND v.value_boolean = 1)"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_has_phase_header():
    assert _sql().startswith("-- phase: data\n")


def test_archive_asset_field_is_seeded_idempotently():
    sql = _sql()
    assert "INSERT INTO asset_custom_field_definitions" in sql
    assert "'Archive Asset', 'Archive Asset', 'checkbox'" in sql
    assert "WHERE LOWER(name) IN ('archive asset', 'archive_asset')" in sql


def test_both_server_reports_exclude_archived_assets():
    sql = _sql()
    for slug in (
        "online-physical-servers-last-30-days",
        "online-virtual-servers-last-30-days",
    ):
        statement = sql.split("WHERE slug = '" + slug + "'")[0].rsplit("UPDATE reporting_queries", 1)[1]
        assert ARCHIVE_FILTER in statement


def _run_report(sql_query: str, archived: int | None) -> list[str]:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE assets (id INTEGER PRIMARY KEY, company_id INT, name TEXT, machine_type TEXT,
            type TEXT, status TEXT, os_name TEXT, serial_number TEXT, last_sync TEXT);
        CREATE TABLE asset_custom_field_definitions (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE asset_custom_field_values (asset_id INT, field_definition_id INT, value_boolean INT);
        INSERT INTO companies VALUES (1, 'Acme');
        INSERT INTO asset_custom_field_definitions VALUES (1, 'Archive Asset');
        INSERT INTO assets VALUES (1, 1, 'srv-kept', 'Physical Server', NULL, 'ok', 'Win', 'S1', DATE('now'));
        INSERT INTO assets VALUES (2, 1, 'srv-archived', 'Physical Server', NULL, 'ok', 'Win', 'S2', DATE('now'));
        INSERT INTO assets VALUES (3, 1, 'vm-kept', 'Virtual Machine', NULL, 'ok', 'Win', 'S3', DATE('now'));
        INSERT INTO assets VALUES (4, 1, 'vm-archived', 'Virtual Machine', NULL, 'ok', 'Win', 'S4', DATE('now'));
        """
    )
    if archived is not None:
        conn.executemany(
            "INSERT INTO asset_custom_field_values VALUES (?, 1, ?)",
            [(1, 0), (2, archived), (3, 0), (4, archived)],
        )
    query = (
        sql_query.replace("''", "'")
        .replace("{{current.company}}", "1")
        .replace("(CURRENT_DATE - INTERVAL 30 DAY)", "DATE('now', '-30 day')")
    )
    return [row[1] for row in conn.execute(query).fetchall()]


def _query_for(slug: str) -> str:
    statement = _sql().split("WHERE slug = '" + slug + "'")[0].rsplit("SET sql_query = '", 1)[1]
    return statement.rstrip().rstrip("'")


def test_reports_drop_only_ticked_archive_assets():
    physical = _query_for("online-physical-servers-last-30-days")
    virtual = _query_for("online-virtual-servers-last-30-days")
    assert sorted(_run_report(physical, 1)) == ["srv-kept"]
    assert sorted(_run_report(virtual, 1)) == ["vm-kept"]
    assert sorted(_run_report(physical, None)) == ["srv-archived", "srv-kept"]
    assert sorted(_run_report(virtual, 0)) == ["vm-archived", "vm-kept"]
