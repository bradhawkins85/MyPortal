from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.core.database import Database


class FakeCursor:
    def __init__(self, conn, cursor_type=None):
        self._conn = conn
        self._cursor_type = cursor_type
        self._fetchone_result = None
        self._fetchall_result = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, params=None):
        self._conn.record(sql, params)
        if sql.startswith("SELECT GET_LOCK"):
            self._fetchone_result = (1,)
        elif sql.startswith("SELECT RELEASE_LOCK"):
            self._conn.released_locks.append(params[0])
        elif sql.startswith("DELETE FROM migrations"):
            self._conn.deleted.append(params[0])
        elif sql.startswith("INSERT INTO migrations"):
            self._conn.inserted.append(params[0])
        elif sql.startswith("CREATE TABLE"):
            self._conn.migration_table_created = True
        elif sql.startswith("SET sql_notes"):
            self._conn.sql_notes_statements.append(sql)
        else:
            self._conn.migration_statements.append(sql)

    async def fetchone(self):
        return self._fetchone_result

    async def fetchall(self):
        return self._fetchall_result or []


class FakeConnection:
    def __init__(self):
        self.statements: list[tuple[str, tuple | None]] = []
        self.deleted: list[str] = []
        self.inserted: list[str] = []
        self.released_locks: list[str] = []
        self.migration_statements: list[str] = []
        self.sql_notes_statements: list[str] = []
        self.migration_table_created = False

    def record(self, sql, params):
        self.statements.append((sql, params))

    def cursor(self, cursor_type=None):
        return FakeCursor(self, cursor_type)


def _build_database(monkeypatch, tmp_path) -> tuple[Database, FakeConnection]:
    database = Database()
    monkeypatch.setattr(database, "connect", AsyncMock())
    fake_conn = FakeConnection()

    @asynccontextmanager
    async def fake_acquire():
        yield fake_conn

    monkeypatch.setattr(database, "acquire", fake_acquire)
    monkeypatch.setattr(database, "_get_migrations_dir", lambda: tmp_path)
    return database, fake_conn


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_reprocess_specific_migration(tmp_path, monkeypatch):
    migration = tmp_path / "001_example.sql"
    migration.write_text("UPDATE foo SET bar = 1;\nUPDATE foo SET baz = 2;", encoding="utf-8")

    database, fake_conn = _build_database(monkeypatch, tmp_path)

    await database.reprocess_migrations(["001_example"])

    assert fake_conn.deleted == ["001_example.sql"]
    assert fake_conn.inserted == ["001_example.sql"]
    assert fake_conn.migration_table_created is True
    assert fake_conn.migration_statements == [
        "UPDATE foo SET bar = 1",
        "UPDATE foo SET baz = 2",
    ]


@pytest.mark.anyio
async def test_reprocess_all_migrations(tmp_path, monkeypatch):
    first = tmp_path / "001_first.sql"
    first.write_text("DELETE FROM table_a;", encoding="utf-8")
    second = tmp_path / "002_second.sql"
    second.write_text("INSERT INTO table_b VALUES (1);", encoding="utf-8")

    database, fake_conn = _build_database(monkeypatch, tmp_path)

    await database.reprocess_migrations()

    assert fake_conn.deleted == ["001_first.sql", "002_second.sql"]
    assert fake_conn.inserted == ["001_first.sql", "002_second.sql"]
    assert fake_conn.migration_statements == [
        "DELETE FROM table_a",
        "INSERT INTO table_b VALUES (1)",
    ]


@pytest.mark.anyio
async def test_reprocess_missing_migration_raises(tmp_path, monkeypatch):
    existing = tmp_path / "001_exists.sql"
    existing.write_text("SELECT 1;", encoding="utf-8")

    database, _ = _build_database(monkeypatch, tmp_path)

    with pytest.raises(ValueError) as exc:
        await database.reprocess_migrations(["missing"])

    assert "missing.sql" in str(exc.value)
