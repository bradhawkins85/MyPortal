"""Coverage for the "tray agent below version" reporting catalogue entries."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.database import Database
from app.services.reporting import uses_company_context, validate_select_query

MIGRATION = (
    Path(__file__).parent.parent / "migrations" / "553_reporting_tray_agent_below_version.sql"
)


def _entries() -> dict[str, tuple[str, str, str, str, int]]:
    statements = Database()._split_sql_statements(MIGRATION.read_text(encoding="utf-8"))
    assert len(statements) == 1
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE reporting_queries (slug TEXT UNIQUE, name TEXT, "
        "description TEXT, category TEXT, sql_query TEXT, is_system INTEGER)"
    )
    conn.execute(statements[0].replace("INSERT IGNORE INTO", "INSERT OR IGNORE INTO", 1))
    rows = conn.execute(
        "SELECT slug, name, description, category, sql_query, is_system FROM reporting_queries"
    ).fetchall()
    return {row[0]: row[1:] for row in rows}


def test_migration_header_and_idempotent_insert():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert sql.startswith("-- phase: data")
    assert "INSERT IGNORE INTO reporting_queries" in sql


def test_reports_use_tray_version_from_assets_not_rmm_agents():
    entries = _entries()
    assert set(entries) == {"tray-agent-below-version", "tray-agent-below-version-company-scoped"}
    for slug, (name, description, category, query, is_system) in entries.items():
        assert is_system == 1
        assert category == "Tray"
        assert "'1.0.0'" in description
        validate_select_query(query)
        assert "FROM assets a" in query
        assert "td.agent_version" in query
        assert "rmm_agents" not in query
        assert "SELECT '1.0.0' AS below_version" in query
        assert uses_company_context(query) == slug.endswith("-company-scoped")
