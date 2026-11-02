"""Migration 536 removes the never-started runs that portal offboarding
requests created before approval, and leaves every other run alone."""

import sqlite3
from pathlib import Path

MIGRATION = Path("migrations/536_remove_duplicate_offboarding_request_runs.sql")


def _database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE staff_onboarding_workflow_executions (
            id INTEGER PRIMARY KEY,
            staff_id INTEGER,
            direction TEXT,
            state TEXT,
            current_step TEXT,
            started_at TEXT,
            completed_at TEXT
        );
        CREATE TABLE staff_onboarding_workflow_step_logs (
            id INTEGER PRIMARY KEY, execution_id INTEGER
        );
        CREATE TABLE staff_onboarding_external_checkpoints (
            id INTEGER PRIMARY KEY, execution_id INTEGER
        );
        """
    )
    conn.executemany(
        "INSERT INTO staff_onboarding_workflow_executions VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            # The duplicate left by the request, and the run queued on approval.
            (106, 9934, "offboarding", "requested", None, None, None),
            (107, 9934, "offboarding", "offboarding_approved", "queued", None, None),
            # A run the scheduler has claimed.
            (108, 1, "offboarding", "requested", "queued", None, None),
            # A run that has started.
            (109, 2, "offboarding", "requested", None, "2026-11-01 10:00:00", None),
            # An onboarding run.
            (110, 3, "onboarding", "requested", None, None, None),
            # Runs with step logs or a checkpoint.
            (111, 4, "offboarding", "requested", None, None, None),
            (112, 5, "offboarding", "requested", None, None, None),
        ],
    )
    conn.execute("INSERT INTO staff_onboarding_workflow_step_logs VALUES (1, 111)")
    conn.execute("INSERT INTO staff_onboarding_external_checkpoints VALUES (1, 112)")
    return conn


def _remaining_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM staff_onboarding_workflow_executions ORDER BY id"
    ).fetchall()
    return [row[0] for row in rows]


def test_migration_declares_a_data_phase():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.startswith("-- phase: data\n")
    assert "-- maintenance: false" in sql


def test_migration_removes_only_never_started_offboarding_request_runs():
    conn = _database()

    conn.executescript(MIGRATION.read_text(encoding="utf-8"))

    assert _remaining_ids(conn) == [107, 108, 109, 110, 111, 112]


def test_migration_is_idempotent():
    conn = _database()
    sql = MIGRATION.read_text(encoding="utf-8")

    conn.executescript(sql)
    conn.executescript(sql)

    assert _remaining_ids(conn) == [107, 108, 109, 110, 111, 112]
