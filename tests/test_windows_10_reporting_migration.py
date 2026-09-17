"""Coverage for the Windows 10 computer system reports."""

import re
from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "370_windows_10_computer_reporting_queries.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_windows_10_reports_are_system_scoped_and_idempotent():
    sql = _sql()
    slugs = re.findall(r"'windows-10-computers-synced-last-(\d+)-days'", sql)

    assert slugs == ["30", "60", "90"]
    assert "INSERT IGNORE INTO reporting_queries" in sql
    assert sql.count("{{current.company}}") == 3
    assert sql.count("\n        1\n") == 3


def test_windows_10_reports_select_requested_fields_and_rolling_windows():
    sql = _sql()
    projection = (
        "SELECT a.name AS computer_name, a.serial_number, a.os_name, "
        "a.last_sync, a.last_user FROM assets a"
    )

    assert sql.count(projection) == 3
    assert sql.count("LIKE ''%windows 10%''") == 3
    assert sql.count("a.last_sync IS NOT NULL") == 3
    for days in (30, 60, 90):
        assert sql.count(f"CURRENT_DATE - INTERVAL {days} DAY") == 1
