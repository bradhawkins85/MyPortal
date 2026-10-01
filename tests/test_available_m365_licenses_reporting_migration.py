"""Regression coverage for the Available licenses dashboard report."""

from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "315_available_m365_licenses_dashboard_report.sql"
)


def test_available_licenses_report_is_added_to_dashboard_catalog():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "INSERT IGNORE INTO reporting_queries" in sql
    assert "'dashboard-global-available-licenses'" in sql
    assert "'Dashboard - Available licenses'" in sql
    assert "GREATEST(license_totals.total_licenses - license_totals.allocated_licenses, 0)" in sql


def test_available_licenses_report_groups_results_by_company_and_product():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "c.id AS company_id" in sql
    assert "c.name AS company" in sql
    assert "lsn.friendly_name" in sql
    assert "ORDER BY license_totals.company ASC, license_totals.product ASC" in sql


def test_available_licenses_report_excludes_hidden_products_and_counts_allocations():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "COALESCE(lsn.hidden, 0) = 0" in sql
    assert "SELECT sl.staff_id FROM staff_licenses" in sql
    assert "SELECT ogm.staff_id FROM group_licenses" in sql
    assert "COUNT(DISTINCT s.id)" in sql
