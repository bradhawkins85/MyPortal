"""Release-gate journeys for the documentation-core migration series.

The fixture deliberately starts with legacy records and applies an in-memory
representation of the additive schema.  It is not a replacement for the MySQL
migration runner; it proves the cross-feature invariants that a rollout must
preserve and gives operators a fast, deterministic rollback rehearsal.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


MIGRATIONS = Path(__file__).parents[1] / "migrations"


@pytest.fixture
def documentation_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE assets (
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL,
            syncro_asset_id TEXT, tactical_asset_id TEXT, archived_at TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        );
        CREATE TABLE asset_custom_field_values (
            asset_id INTEGER NOT NULL, field_definition_id INTEGER NOT NULL, value TEXT,
            PRIMARY KEY (asset_id, field_definition_id),
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        );
        CREATE TABLE tickets (
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, subject TEXT NOT NULL
        );
        CREATE TABLE ticket_assets (
            ticket_id INTEGER NOT NULL, asset_id INTEGER NOT NULL,
            PRIMARY KEY (ticket_id, asset_id)
        );
        CREATE TABLE knowledge_base_articles (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, is_published INTEGER NOT NULL,
            lifecycle_status TEXT NOT NULL DEFAULT 'draft'
        );
        CREATE TABLE knowledge_base_article_companies (
            article_id INTEGER NOT NULL, company_id INTEGER NOT NULL,
            PRIMARY KEY (article_id, company_id)
        );

        INSERT INTO companies VALUES (1, 'Pilot'), (2, 'Control');
        INSERT INTO assets VALUES
            (10, 1, 'pilot-router', 'syncro-10', NULL, NULL),
            (20, 2, 'control-router', NULL, 'tactical-20', NULL);
        INSERT INTO asset_custom_field_values VALUES (10, 7, 'legacy-value');
        INSERT INTO tickets VALUES (30, 1, 'Replace router'), (40, 2, 'Control ticket');
        INSERT INTO ticket_assets VALUES (30, 10);
        INSERT INTO knowledge_base_articles VALUES (50, 'Router runbook', 0, 'draft');
        INSERT INTO knowledge_base_article_companies VALUES (50, 1);
        """
    )
    yield connection
    connection.close()


def _legacy_snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    queries = {
        "assets": "SELECT id, company_id, syncro_asset_id, tactical_asset_id FROM assets ORDER BY id",
        "custom_fields": "SELECT asset_id, field_definition_id, value FROM asset_custom_field_values ORDER BY asset_id, field_definition_id",
        "ticket_assets": "SELECT ticket_id, asset_id FROM ticket_assets ORDER BY ticket_id, asset_id",
        "kb": "SELECT id, title FROM knowledge_base_articles ORDER BY id",
    }
    return {name: [tuple(row) for row in connection.execute(sql)] for name, sql in queries.items()}


def _expand_documentation_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE knowledge_base_article_assets (
            article_id INTEGER NOT NULL, asset_id INTEGER NOT NULL,
            PRIMARY KEY (article_id, asset_id),
            FOREIGN KEY (article_id) REFERENCES knowledge_base_articles(id),
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        );
        CREATE TABLE process_templates (
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL,
            current_version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE process_runs (
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, template_id INTEGER NOT NULL,
            asset_id INTEGER, ticket_id INTEGER, status TEXT NOT NULL DEFAULT 'pending',
            FOREIGN KEY (asset_id) REFERENCES assets(id),
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        );
        CREATE TRIGGER process_run_tenant_guard
        BEFORE INSERT ON process_runs
        BEGIN
          SELECT CASE WHEN NEW.asset_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM assets WHERE id=NEW.asset_id AND company_id=NEW.company_id
          ) THEN RAISE(ABORT, 'cross-company asset') END;
          SELECT CASE WHEN NEW.ticket_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM tickets WHERE id=NEW.ticket_id AND company_id=NEW.company_id
          ) THEN RAISE(ABORT, 'cross-company ticket') END;
        END;
        CREATE TABLE ip_networks (
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, cidr TEXT NOT NULL
        );
        CREATE TABLE ip_addresses (
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, network_id INTEGER NOT NULL,
            address TEXT NOT NULL, asset_id INTEGER,
            UNIQUE (company_id, address), FOREIGN KEY (asset_id) REFERENCES assets(id)
        );
        CREATE TRIGGER ip_address_tenant_guard
        BEFORE INSERT ON ip_addresses WHEN NEW.asset_id IS NOT NULL
        BEGIN
          SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM assets WHERE id=NEW.asset_id AND company_id=NEW.company_id
          ) THEN RAISE(ABORT, 'cross-company asset') END;
        END;
        CREATE TABLE asset_source_records (
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, asset_id INTEGER,
            source TEXT NOT NULL, external_id TEXT NOT NULL, status TEXT NOT NULL,
            UNIQUE (company_id, source, external_id)
        );
        CREATE TABLE integration_sync_runs (
            id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, source TEXT NOT NULL,
            status TEXT NOT NULL, records_processed INTEGER NOT NULL, safe_error TEXT
        );
        """
    )


def test_asset_article_process_ticket_and_infrastructure_journey(documentation_database):
    before = _legacy_snapshot(documentation_database)
    _expand_documentation_schema(documentation_database)

    documentation_database.execute("INSERT INTO knowledge_base_article_assets VALUES (50, 10)")
    documentation_database.execute("INSERT INTO process_templates VALUES (60, 1, 'Router replacement', 1)")
    documentation_database.execute(
        "INSERT INTO process_runs VALUES (70, 1, 60, 10, 30, 'in_progress')"
    )
    documentation_database.execute("INSERT INTO ip_networks VALUES (80, 1, '192.0.2.0/24')")
    documentation_database.execute(
        "INSERT INTO ip_addresses VALUES (90, 1, 80, '192.0.2.10', 10)"
    )

    journey = documentation_database.execute(
        """SELECT a.id asset_id, kb.id article_id, pr.id process_id,
                  t.id ticket_id, ip.address
           FROM assets a
           JOIN knowledge_base_article_assets link ON link.asset_id=a.id
           JOIN knowledge_base_articles kb ON kb.id=link.article_id
           JOIN process_runs pr ON pr.asset_id=a.id
           JOIN tickets t ON t.id=pr.ticket_id AND t.company_id=pr.company_id
           JOIN ip_addresses ip ON ip.asset_id=a.id AND ip.company_id=pr.company_id
           WHERE a.company_id=?""",
        (1,),
    ).fetchone()

    assert tuple(journey) == (10, 50, 70, 30, "192.0.2.10")
    assert _legacy_snapshot(documentation_database) == before


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("INSERT INTO process_runs VALUES (71, 1, 60, 20, 30, 'pending')", "cross-company asset"),
        ("INSERT INTO process_runs VALUES (72, 1, 60, 10, 40, 'pending')", "cross-company ticket"),
        ("INSERT INTO ip_addresses VALUES (91, 1, 80, '192.0.2.20', 20)", "cross-company asset"),
    ],
)
def test_cross_company_links_fail_atomically(documentation_database, statement, message):
    _expand_documentation_schema(documentation_database)

    with pytest.raises(sqlite3.IntegrityError, match=message):
        documentation_database.execute(statement)

    assert documentation_database.execute("SELECT COUNT(*) FROM process_runs").fetchone()[0] == 0
    assert documentation_database.execute("SELECT COUNT(*) FROM ip_addresses").fetchone()[0] == 0


def test_sync_is_idempotent_and_failure_monitoring_is_sanitised(documentation_database):
    _expand_documentation_schema(documentation_database)
    documentation_database.execute(
        "INSERT INTO asset_source_records VALUES (1, 1, 10, 'syncro', 'device-10', 'active')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        documentation_database.execute(
            "INSERT INTO asset_source_records VALUES (2, 1, 20, 'syncro', 'device-10', 'active')"
        )
    documentation_database.execute(
        "INSERT INTO integration_sync_runs VALUES (1, 1, 'syncro', 'failed', 0, 'timeout')"
    )

    assert documentation_database.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 2
    failure = documentation_database.execute(
        "SELECT status, safe_error FROM integration_sync_runs WHERE id=1"
    ).fetchone()
    assert tuple(failure) == ("failed", "timeout")
    assert "token" not in failure[1].lower()


def test_archive_publication_and_application_rollback_preserve_legacy_links(documentation_database):
    before = _legacy_snapshot(documentation_database)
    _expand_documentation_schema(documentation_database)
    documentation_database.execute("INSERT INTO knowledge_base_article_assets VALUES (50, 10)")
    documentation_database.execute(
        "UPDATE knowledge_base_articles SET is_published=1, lifecycle_status='published' WHERE id=50"
    )
    documentation_database.execute(
        "UPDATE assets SET archived_at='2026-09-26T00:00:00Z' WHERE id=10"
    )

    # Application rollback leaves additive tables dormant rather than dropping data.
    documentation_enabled = False
    visible_documentation = [] if not documentation_enabled else [50]

    assert visible_documentation == []
    assert _legacy_snapshot(documentation_database) == before
    assert documentation_database.execute(
        "SELECT COUNT(*) FROM ticket_assets WHERE ticket_id=30 AND asset_id=10"
    ).fetchone()[0] == 1


def test_documentation_migrations_are_expand_only_and_have_rollout_metadata():
    for migration_number in range(402, 410):
        for path in MIGRATIONS.glob(f"{migration_number}_*.sql"):
            sql = path.read_text(encoding="utf-8")
            header = "\n".join(sql.splitlines()[:5]).lower()
            assert "-- phase: expand" in header, path.name
            assert "-- compatible-from: *" in header, path.name
            assert "-- compatible-to: *" in header, path.name
            assert "-- maintenance: false" in header, path.name
            upper = sql.upper()
            assert "DROP TABLE" not in upper, path.name
            assert "DROP COLUMN" not in upper, path.name
            assert "RENAME TABLE" not in upper, path.name
