"""Secondary DDL must never run on the main database or hide failed upgrades."""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.database import Database
from app.core import event_log_database


MIGRATION = Path(__file__).resolve().parents[1] / "migrations/528_rmm_event_log_repeats_index.sql"


def test_history_truncation_requires_maintenance_on_upgrades():
    database = Database()
    metadata = database._migration_metadata(MIGRATION)
    with pytest.raises(RuntimeError, match="requires UPG01 maintenance mode"):
        database._validate_migration_compatibility(MIGRATION, metadata, None, None, False)
    database._validate_migration_compatibility(MIGRATION, metadata, None, None, True)


@pytest.mark.asyncio
async def test_event_log_migration_stays_pending_until_configured_and_routes_ddl(tmp_path, monkeypatch):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / MIGRATION.name).write_bytes(MIGRATION.read_bytes())
    database = Database()
    database._use_sqlite = True
    monkeypatch.setattr(database, "_get_sqlite_path", lambda: tmp_path / "main.db")
    monkeypatch.setattr(database, "_get_migrations_dir", lambda: migrations)
    secondary = event_log_database.event_log_db
    monkeypatch.setattr(secondary, "is_configured", lambda: False)
    execute = AsyncMock()
    monkeypatch.setattr(secondary, "run_sql_migration", execute)
    try:
        await database.run_migrations(maintenance=True)
        assert await database.fetch_all("SELECT name FROM migrations") == []
        execute.assert_not_awaited()

        monkeypatch.setattr(secondary, "is_configured", lambda: True)
        execute.side_effect = RuntimeError("index build failed")
        with pytest.raises(RuntimeError, match="failed after partial DDL"):
            await database.run_migrations(maintenance=True)
        assert (await database.fetch_one("SELECT state FROM migrations"))["state"] == "failed"

        execute.side_effect = None
        await database.run_migrations(maintenance=True)
        assert (await database.fetch_one("SELECT state FROM migrations"))["state"] == "completed"
        ddl = execute.call_args.args[0]
        assert len(ddl) == 2
        assert ddl[0] == "TRUNCATE TABLE rmm_event_log_entries"
        assert "ADD INDEX IF NOT EXISTS idx_rmm_event_log_repeats" in ddl[1]
        assert "(agent_id, message_id, time_created)" in ddl[1]
        assert "ALGORITHM=INPLACE" in ddl[1] and "LOCK=NONE" in ddl[1]
        # A second deployment skips completed secondary migrations.
        count = execute.await_count
        await database.run_migrations()
        assert execute.await_count == count
        assert await database.fetch_all(
            "SELECT name FROM sqlite_master WHERE name = 'rmm_event_log_entries'"
        ) == []
    finally:
        await database.disconnect()
