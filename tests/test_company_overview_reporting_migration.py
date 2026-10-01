"""Coverage for Company Overview segments seeded into Reporting."""

import re
from pathlib import Path

from app.services.reports import REPORT_SECTIONS


MIGRATION = Path(__file__).parent.parent / "migrations" / "316_company_overview_reporting_queries.sql"


def test_migration_seeds_one_reporting_query_per_company_overview_section():
    sql = MIGRATION.read_text(encoding="utf-8")
    slugs = re.findall(r"\('([^']+)',\s*'([^']+)'", sql)

    assert len(slugs) == len(REPORT_SECTIONS)
    assert len({slug for slug, _title in slugs}) == len(REPORT_SECTIONS)
    assert all(slug.startswith("report-") for slug, _title in slugs)
    assert all(title.startswith("Report - ") for _slug, title in slugs)


def test_company_overview_reporting_queries_are_system_scoped_and_idempotent():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "INSERT IGNORE INTO reporting_queries" in sql
    assert sql.count("{{current.company}}") == len(REPORT_SECTIONS)
    assert sql.count(", 1)") == len(REPORT_SECTIONS)
