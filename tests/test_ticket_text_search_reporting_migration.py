"""Regression coverage for ticket text-search reporting catalogue entries."""

import re
from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "384_ticket_text_search_reporting_queries.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_ticket_search_reports_cover_every_and_or_field_combination() -> None:
    sql = _sql()
    slugs = re.findall(r"\('((?:tickets-search-)[^']+)'", sql)

    assert len(slugs) == 11
    assert len(set(slugs)) == 11
    assert "tickets-search-subject-or-description" in slugs
    assert "tickets-search-all-fields-and" in slugs
    assert "tickets-search-all-fields-or" in slugs


def test_ticket_search_reports_are_idempotent_system_queries_and_company_scoped() -> None:
    sql = _sql()

    assert "INSERT IGNORE INTO reporting_queries" in sql
    assert sql.count("{{current.company}}") == 11
    assert sql.count("ORDER BY t.updated_at DESC, t.id DESC") == 11
    assert sql.count(", 1)") == 11


def test_ticket_search_reports_output_ticket_lists_and_search_all_requested_fields() -> None:
    sql = _sql()
    projection = (
        "SELECT t.id AS ticket_id, t.subject, t.description AS initial_description, "
        "t.ai_tags, t.status, t.priority, t.created_at, t.updated_at FROM tickets t"
    )

    assert sql.count(projection) == 11
    assert "COALESCE(t.subject, '''') LIKE" in sql
    assert "COALESCE(t.description, '''') LIKE" in sql
    assert "COALESCE(t.ai_tags, '''') LIKE" in sql
    assert "AND (COALESCE(t.subject, '''') LIKE ''%search text%'' OR " in sql
