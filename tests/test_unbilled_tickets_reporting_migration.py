"""Regression coverage for the Unbilled Tickets By Company system report."""

from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "302_unbilled_tickets_by_company_report.sql"
)


def test_unbilled_tickets_report_groups_unbilled_time_by_company_and_labour_type():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "name = 'Unbilled Tickets By Company'" in sql
    assert "SUM(tr.minutes_spent) AS billable_minutes" in sql
    assert "GROUP BY c.id, c.name, lt.id, lt.name" in sql
    assert "tr.is_billable = 1" in sql
    assert "tr.minutes_spent > 0" in sql
    assert "NOT EXISTS" in sql
    assert "bte.reply_id = tr.id" in sql


def test_unbilled_tickets_report_only_updates_the_system_seed():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "WHERE slug = 'unbilled-tickets'" in sql
    assert "AND is_system = 1" in sql
