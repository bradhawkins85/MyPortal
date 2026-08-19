"""Coverage for the system reports backed by network discovery data."""

import re
from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "327_discovered_device_reporting_queries.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_discovered_device_reports_are_system_scoped_and_idempotent():
    sql = _sql()
    slugs = re.findall(r"\n\s*'([^']+)',\n\s*'Discovered Devices", sql)

    assert len(slugs) == 5
    assert len(set(slugs)) == 5
    assert "INSERT IGNORE INTO reporting_queries" in sql
    assert sql.count("{{current.company}}") == 5
    assert sql.count("\n        1\n") == 5


def test_discovered_device_summary_reports_include_requested_fields():
    sql = _sql().lower()

    for field in (
        " as name",
        " as local_ip",
        " as vendor",
        " as first_seen",
        " as last_seen",
        " as discovered_by",
        " as myportal_asset_status",
        "nd.description",
    ):
        assert sql.count(field) >= 3

    assert "agent not required" in sql
    assert "agent may be required" in sql
    assert "matched: " in sql


def test_discovered_device_reports_offer_date_all_details_and_scanner_variants():
    sql = _sql()

    assert sql.count("CURRENT_DATE - INTERVAL 30 DAY") == 3
    assert "'discovered-devices-all-details-last-30-days'" in sql
    assert "'discovered-devices-all-details'" in sql
    for field in (
        "nd.wan_ip",
        "nd.mac_address",
        "nd.os_details",
        "nd.open_ports",
        "nd.state",
        "nd.device_type_id",
        "nd.agent_not_required",
        "a.status AS matched_asset_status",
    ):
        assert sql.count(field) >= 2

    assert "COALESCE(scanner_asset.name, td.hostname, '''') LIKE ''%''" in sql
    assert "replace the % wildcard" in sql
