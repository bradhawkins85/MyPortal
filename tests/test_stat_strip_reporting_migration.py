"""Regression coverage for stat-strip reporting catalogue entries."""

import hashlib
import re
from pathlib import Path


MIGRATION = Path("migrations/381_stat_strip_reporting_queries.sql")
M365_FIX_MIGRATION = Path("migrations/383_fix_m365_best_practices_stat_strip.sql")

# One reporting entry may back identical summary/detail or admin/dashboard strips.
# Keeping this map beside the migration makes additions to the UI inventory visible
# in review and prevents silently omitting a newly introduced strip.
TEMPLATE_REPORTS = {
    "app/templates/admin/backup_jobs.html": "stat-strip-backup-today",
    "app/templates/admin/backup_summary.html": "stat-strip-backup-today",
    "app/templates/admin/rag.html": ("stat-strip-rag-index", "stat-strip-rag-matching"),
    "app/templates/admin/service_status.html": "stat-strip-service-status-catalogue",
    "app/templates/admin/tickets.html": "stat-strip-admin-tickets",
    "app/templates/bcp/overview.html": "stat-strip-bcp-readiness",
    "app/templates/defender/index.html": "stat-strip-defender-overview",
    "app/templates/dmarc/index.html": "stat-strip-dmarc-overview",
    "app/templates/m365/best_practices.html": "stat-strip-m365-best-practices-live",
    "app/templates/notifications/index.html": "stat-strip-notifications",
    "app/templates/reports/_sections/asset_custom_fields.html": "stat-strip-report-asset-custom-fields",
    "app/templates/reports/_sections/asset_custom_fields_detail.html": "stat-strip-report-asset-custom-fields",
    "app/templates/reports/_sections/assets.html": "stat-strip-report-assets",
    "app/templates/reports/_sections/backup_jobs.html": "stat-strip-report-backups",
    "app/templates/reports/_sections/compliance_checks.html": "stat-strip-report-compliance",
    "app/templates/reports/_sections/essential8.html": "stat-strip-report-essential-8",
    "app/templates/reports/_sections/essential8_ml1.html": "stat-strip-report-essential-8",
    "app/templates/reports/_sections/essential8_ml2.html": "stat-strip-report-essential-8",
    "app/templates/reports/_sections/essential8_ml3.html": "stat-strip-report-essential-8",
    "app/templates/reports/_sections/huntress_edr.html": "stat-strip-report-huntress-edr",
    "app/templates/reports/_sections/huntress_edr_detail.html": "stat-strip-report-huntress-edr",
    "app/templates/reports/_sections/huntress_itdr.html": "stat-strip-report-huntress-itdr",
    "app/templates/reports/_sections/huntress_itdr_detail.html": "stat-strip-report-huntress-itdr",
    "app/templates/reports/_sections/huntress_sat.html": "stat-strip-report-huntress-sat",
    "app/templates/reports/_sections/huntress_sat_detail.html": "stat-strip-report-huntress-sat",
    "app/templates/reports/_sections/huntress_siem.html": "stat-strip-report-huntress-siem",
    "app/templates/reports/_sections/huntress_siem_detail.html": "stat-strip-report-huntress-siem",
    "app/templates/reports/_sections/huntress_soc.html": "stat-strip-report-huntress-soc",
    "app/templates/reports/_sections/huntress_soc_detail.html": "stat-strip-report-huntress-soc",
    "app/templates/reports/_sections/m365_best_practices.html": "stat-strip-report-m365-best-practices",
    "app/templates/reports/_sections/orders_current_month.html": "stat-strip-report-orders-month",
    "app/templates/reports/_sections/staff.html": "stat-strip-report-active-users",
    "app/templates/reports/_sections/tickets_last_month.html": "stat-strip-report-tickets-month",
    "app/templates/reports/_sections/voice_monitor.html": "stat-strip-report-voice-monitor",
    "app/templates/service_status/dashboard.html": "stat-strip-service-status-company",
    "app/templates/service_status/public_dashboard.html": "stat-strip-service-status-company",
    "app/templates/subscriptions/index.html": "stat-strip-subscription-renewals",
    "app/templates/tickets/index.html": "stat-strip-customer-tickets",
}


def _catalogue_slugs(sql: str) -> list[str]:
    return re.findall(r"^\('([^']+)', 'Stat Strip -", sql, re.MULTILINE)


def test_every_stat_strip_template_has_a_catalogue_mapping() -> None:
    actual = {
        str(path)
        for path in Path("app/templates").rglob("*.html")
        if path.name != "counters.html"
        and ("counter_strip" in path.read_text() or 'class="stat-strip' in path.read_text())
    }
    assert actual == set(TEMPLATE_REPORTS)


def test_applied_stat_strip_catalogue_migration_remains_immutable() -> None:
    # Migration 381 has shipped and its checksum is stored by deployed databases.
    # Corrections must be made in a new migration (such as migration 383).
    assert hashlib.sha256(MIGRATION.read_bytes()).hexdigest() == (
        "2a23ffee84344a782b728342a1a752dd15e06950a12e2f958833d586d18a4bd5"
    )


def test_stat_strip_catalogue_has_every_mapping_once() -> None:
    sql = MIGRATION.read_text()
    slugs = _catalogue_slugs(sql)
    expected = {
        slug
        for mapping in TEMPLATE_REPORTS.values()
        for slug in ((mapping,) if isinstance(mapping, str) else mapping)
    }
    assert len(slugs) == len(set(slugs)), "reporting slugs must not be duplicated"
    assert set(slugs) == expected
    assert sql.count("{{current.company}}") >= 20
    assert "INSERT IGNORE INTO reporting_queries" in sql


def test_m365_best_practices_strip_uses_all_visible_current_results() -> None:
    sql = M365_FIX_MIGRATION.read_text()

    assert "run_at = (SELECT MAX(run_at)" not in sql
    assert "LEFT JOIN m365_best_practice_settings" in sql
    assert "(s.enabled = 1 OR s.check_id IS NULL)" in sql
    assert "LEFT JOIN m365_best_practice_company_exclusions" in sql
    assert "e.check_id IS NULL" in sql
    assert "WHERE slug = 'stat-strip-m365-best-practices-live'" in sql
