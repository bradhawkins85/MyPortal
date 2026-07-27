"""Regression coverage for the Unbilled Tickets By Company system report."""

from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "302_unbilled_tickets_by_company_report.sql"
)
ORIGINAL_REPORT_MIGRATION = (
    Path(__file__).parent.parent / "migrations" / "246_reporting_queries.sql"
)
RESTORE_MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "303_restore_unbilled_tickets_report.sql"
)


def test_unbilled_tickets_report_groups_unbilled_time_by_company_and_labour_type():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "'Unbilled Tickets By Company'," in sql
    assert "SUM(tr.minutes_spent) AS billable_minutes" in sql
    assert "GROUP BY c.id, c.name, lt.id, lt.name" in sql
    assert "tr.is_billable = 1" in sql
    assert "tr.minutes_spent > 0" in sql
    assert "NOT EXISTS" in sql
    assert "bte.reply_id = tr.id" in sql


def test_company_summary_is_added_without_replacing_unbilled_tickets_report():
    sql = MIGRATION.read_text(encoding="utf-8")
    original_sql = ORIGINAL_REPORT_MIGRATION.read_text(encoding="utf-8")

    assert "INSERT IGNORE INTO reporting_queries" in sql
    assert "'unbilled-tickets-by-company'" in sql
    assert "WHERE slug = 'unbilled-tickets'" not in sql
    assert "UPDATE reporting_queries" not in sql
    assert "'unbilled-tickets'," in original_sql
    assert "'Unbilled Tickets'," in original_sql


def test_original_report_is_restored_for_installations_with_old_migration():
    sql = RESTORE_MIGRATION.read_text(encoding="utf-8")

    assert "name = 'Unbilled Tickets'" in sql
    assert "WHERE slug = 'unbilled-tickets'" in sql
    assert "AND is_system = 1" in sql
