import sqlite3
from pathlib import Path

from app.core.database import Database


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "446_expand_m365_best_practice_statuses.sql"
)


def test_migration_preserves_results_and_accepts_all_service_outcomes():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE m365_best_practice_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INT NOT NULL,
            check_id VARCHAR(100) NOT NULL,
            check_name VARCHAR(255) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'unknown',
            details TEXT,
            run_at DATETIME NOT NULL,
            remediation_status VARCHAR(20),
            remediated_at DATETIME,
            affected_accounts TEXT,
            notes TEXT,
            remediation_failure_reason TEXT,
            CONSTRAINT chk_m365_bp_status CHECK (
                status IN ('pass', 'fail', 'unknown', 'not_applicable')
            )
        );
        INSERT INTO m365_best_practice_results
            (company_id, check_id, check_name, status, details, run_at, notes)
        VALUES (7, 'existing', 'Existing check', 'pass', 'kept',
                '2026-09-30 20:15:50', 'operator note');
        """
    )

    sql = Database()._adapt_sql_for_sqlite(MIGRATION.read_text(encoding="utf-8"))
    connection.executescript(sql)

    preserved = connection.execute(
        "SELECT company_id, status, details, notes "
        "FROM m365_best_practice_results WHERE check_id = 'existing'"
    ).fetchone()
    assert preserved == (7, "pass", "kept", "operator note")

    outcomes = (
        "pass",
        "fail",
        "unknown",
        "not_applicable",
        "not_licensed",
        "unsupported",
        "permission_missing",
        "assessment_failed",
    )
    for position, outcome in enumerate(outcomes, start=1):
        connection.execute(
            "INSERT INTO m365_best_practice_results "
            "(company_id, check_id, check_name, status, run_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (7, f"new-{position}", "New check", outcome, "2026-09-30 20:16:00"),
        )


def test_migration_has_green_blue_deployment_metadata():
    header = "\n".join(MIGRATION.read_text(encoding="utf-8").splitlines()[:4])
    assert "-- phase: expand" in header
    assert "-- compatible-from: *" in header
    assert "-- compatible-to: *" in header
    assert "-- maintenance: false" in header
