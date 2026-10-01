"""Coverage for the portal-wide subnet scanner system report."""

from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "341_global_subnet_scanners_report.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_subnet_scanner_report_is_system_scoped_and_idempotent():
    sql = _sql()

    assert "INSERT IGNORE INTO reporting_queries" in sql
    assert "'global-subnet-scanners'" in sql
    assert "'Subnet Scanners - All Companies'" in sql
    assert sql.rstrip().endswith(");")


def test_subnet_scanner_report_lists_enabled_devices_for_every_company():
    sql = _sql()

    assert "c.name AS company" in sql
    assert "AS scanner_device" in sql
    assert "nss.subnet" in sql
    assert "td.network_scanner_enabled = 1" in sql
    assert "{{current.company}}" not in sql


def test_subnet_scanner_report_keeps_scanners_without_recorded_subnets():
    sql = _sql()

    assert "LEFT JOIN network_scan_subnets" in sql
    assert "nss.scanner_tray_device_id = td.id" in sql
    assert "nss.company_id = td.company_id" in sql
    assert "No subnet recorded" in sql
