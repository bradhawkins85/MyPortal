from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "359_m365_best_practice_notes.sql"
)


def test_m365_best_practice_notes_migration_adds_notes_column_and_updates_report_query():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ALTER TABLE m365_best_practice_results" in sql
    assert "ADD COLUMN notes TEXT NULL" in sql
    assert "UPDATE reporting_queries" in sql
    assert "SELECT r.check_id, r.check_name, r.status, r.details, r.notes" in sql
    assert "WHERE slug = 'report-m365-best-practice-summary'" in sql
