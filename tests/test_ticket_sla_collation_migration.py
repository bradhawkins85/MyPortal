from pathlib import Path


MIGRATION = Path(__file__).parent.parent / "migrations" / "345_normalize_ticket_sla_collations.sql"


def test_ticket_sla_comparison_columns_use_ticket_collation() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "alter table sla_template_targets" in sql
    assert "alter table sla_template_pause_statuses" in sql
    assert "alter table ticket_status_history" in sql
    assert sql.count("collate utf8mb4_unicode_ci") == 3
