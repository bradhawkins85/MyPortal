"""Coverage for the reporting catalogue entries added for recent features."""

import re
import sqlite3
from pathlib import Path

from app.core.database import Database
from app.services.reporting import (
    _SENSITIVE_COLUMN_PATTERN,
    uses_company_context,
    validate_select_query,
)


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "523_reporting_catalogue_new_features.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _entries() -> list[tuple[str, str, str, str, int]]:
    """Load the migration's rows into an in-memory table and read them back."""
    statements = Database()._split_sql_statements(_sql())
    assert len(statements) == 1
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE reporting_queries (slug TEXT UNIQUE, name TEXT, "
        "description TEXT, sql_query TEXT, is_system INTEGER)"
    )
    conn.execute(statements[0].replace("INSERT IGNORE INTO", "INSERT OR IGNORE INTO", 1))
    return conn.execute(
        "SELECT slug, name, description, sql_query, is_system FROM reporting_queries"
    ).fetchall()


def _existing_slugs() -> set[str]:
    slugs: set[str] = set()
    for path in MIGRATION.parent.glob("*.sql"):
        if path != MIGRATION and "reporting_queries" in path.read_text(encoding="utf-8"):
            slugs.update(re.findall(r"'([a-z0-9]+(?:-[a-z0-9]+)+)'", path.read_text(encoding="utf-8")))
    return slugs


def test_catalogue_entries_are_idempotent_system_entries():
    sql = _sql()
    entries = _entries()

    assert "-- phase: data" in sql.splitlines()[0]
    assert "INSERT IGNORE INTO reporting_queries" in sql
    assert len(entries) >= 100
    assert all(is_system == 1 for *_, is_system in entries)
    assert all(len(slug) <= 120 and len(name) <= 255 for slug, name, *_ in entries)


def test_catalogue_slugs_do_not_clash_with_existing_entries():
    slugs = [slug for slug, *_ in _entries()]

    assert len(slugs) == len(set(slugs))
    assert not set(slugs) & _existing_slugs()


def test_every_catalogue_query_passes_reporting_validation():
    for slug, _name, description, query, _ in _entries():
        assert description, slug
        assert validate_select_query(query) == query, slug


def test_catalogue_queries_never_alias_columns_that_reporting_redacts():
    for slug, *_rest in _entries():
        query = _rest[2]
        for alias in re.findall(r"\bAS (\w+)", query):
            assert not _SENSITIVE_COLUMN_PATTERN.search(alias), (slug, alias)


def test_company_report_sections_and_widgets_are_scoped_to_the_current_company():
    for slug, name, _description, query, _ in _entries():
        company_scoped = slug.startswith(("report-", "stat-strip-report-")) or (
            slug.startswith("dashboard-") and not slug.startswith("dashboard-global-")
        )
        assert uses_company_context(query) == company_scoped, slug
        if slug.startswith("report-"):
            assert name.startswith("Report - "), slug
        if slug.startswith("stat-strip-"):
            assert name.startswith("Stat Strip - "), slug


def test_dashboard_widgets_return_x_and_y_columns():
    widgets = [entry for entry in _entries() if entry[0].startswith("dashboard-")]

    assert widgets
    for slug, name, _description, query, _ in widgets:
        assert name.startswith("Dashboard - "), slug
        assert query.startswith("SELECT ") and " AS X, COUNT(*) AS Y " in query, slug


def test_catalogue_covers_the_new_feature_areas():
    slugs = {slug for slug, *_ in _entries()}

    for expected in (
        "rmm-agents-by-company",
        "rmm-hardware-inventory",
        "rmm-software-most-installed",
        "rmm-windows-updates-missing-by-device",
        "rmm-script-run-outcomes-30-days",
        "rmm-checks-failing-now",
        "rmm-findings-open",
        "health-checks-latest-by-asset",
        "tray-deployment-links",
        "assets-warranty-expiring-next-90-days",
        "llm-usage-by-feature-30-days",
        "marketing-campaign-performance",
        "custom-form-submissions-by-form",
        "client-onboarding-pipeline",
        "report-rmm-devices",
        "stat-strip-rmm-overview",
    ):
        assert expected in slugs


def test_latest_health_check_reports_use_the_newest_completed_check_per_device():
    latest = (
        "JOIN (SELECT asset_id, MAX(id) AS id FROM ai_health_checks "
        "WHERE status = 'completed' GROUP BY asset_id) latest ON latest.id = h.id"
    )
    queries = {slug: query for slug, _n, _d, query, _ in _entries()}

    for slug in (
        "health-checks-latest-by-asset",
        "health-checks-score-by-company",
        "report-health-checks",
        "stat-strip-health-checks",
        "stat-strip-report-health-checks",
        "dashboard-health-check-status",
    ):
        assert latest in queries[slug], slug
