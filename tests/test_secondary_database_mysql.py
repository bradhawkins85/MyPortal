"""Startup DDL of a MySQL secondary database must not queue on metadata locks."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.core import secondary_database
from app.core.secondary_database import Connection, Index, SecondaryDatabase

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS existing_table (id INT)",
    "CREATE TABLE IF NOT EXISTS new_table (id INT)",
)


class FakeCursor:
    def __init__(self, server: "FakeServer") -> None:
        self.server = server
        self._rows: list[tuple] = []

    async def execute(self, sql: str, params: tuple = ()) -> None:
        self.server.statements.append(sql)
        self._rows = []
        if "information_schema.TABLES" in sql:
            self._rows = [(name,) for name in self.server.tables]
        elif "information_schema.STATISTICS" in sql:
            if params[1] in self.server.indexes:
                self._rows = [(1,)]
        elif "GET_LOCK" in sql:
            self._rows = [(1 if self.server.lock_free else 0,)]

    async def fetchall(self) -> list[tuple]:
        return self._rows

    async def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self, server: "FakeServer") -> None:
        self.server = server

    @asynccontextmanager
    async def cursor(self, *args):
        yield FakeCursor(self.server)


class FakeServer:
    def __init__(self, *, lock_free: bool = True) -> None:
        self.tables = {"existing_table"}
        self.indexes: set[str] = set()
        self.lock_free = lock_free
        self.statements: list[str] = []

    @asynccontextmanager
    async def acquire(self):
        yield FakeConn(self)

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


def _database(server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> SecondaryDatabase:
    database = SecondaryDatabase(
        label="Test",
        env_prefix="TEST_DB",
        settings=lambda: Connection(host="db", port=3306, user="u", password="p", name="test"),
        mysql_schema=SCHEMA,
        sqlite_schema=(),
        indexes=(Index("existing_table", "idx_new", "id"),),
    )

    async def pool() -> FakeServer:
        return server

    monkeypatch.setattr(database, "_create_mysql_pool", pool)
    return database


@pytest.mark.anyio
async def test_connect_only_creates_missing_tables_with_a_lock_timeout(monkeypatch):
    server = FakeServer()
    database = _database(server, monkeypatch)

    assert await database.connect()
    await database._index_task

    startup = server.statements[: server.statements.index("SET SESSION lock_wait_timeout = DEFAULT") + 1]
    assert startup[0] == f"SET SESSION lock_wait_timeout = {secondary_database.LOCK_WAIT_SECONDS}"
    assert SCHEMA[0] not in server.statements
    assert SCHEMA[1] in startup


@pytest.mark.anyio
async def test_indexes_are_built_online_by_the_worker_holding_the_lock(monkeypatch):
    server = FakeServer()
    database = _database(server, monkeypatch)

    assert await database.connect()
    await database._index_task

    alters = [sql for sql in server.statements if sql.startswith("ALTER TABLE")]
    assert alters == ["ALTER TABLE existing_table ADD INDEX idx_new (id), ALGORITHM=INPLACE, LOCK=NONE"]
    assert any("RELEASE_LOCK" in sql for sql in server.statements)


@pytest.mark.anyio
async def test_other_workers_skip_the_index_build(monkeypatch):
    server = FakeServer(lock_free=False)
    database = _database(server, monkeypatch)

    assert await database.connect()
    await database._index_task

    assert not [sql for sql in server.statements if sql.startswith("ALTER TABLE")]
    assert not any("RELEASE_LOCK" in sql for sql in server.statements)


@pytest.mark.anyio
async def test_sql_migration_waits_for_index_lock_and_propagates_failure(monkeypatch):
    server = FakeServer(lock_free=False)
    database = _database(server, monkeypatch)
    with pytest.raises(RuntimeError, match="migration lock"):
        await database.run_sql_migration(["ALTER TABLE existing_table ADD INDEX idx_new (id)"])
    assert not any(sql.startswith("ALTER TABLE") for sql in server.statements)

    server.lock_free = True
    original_execute = FakeCursor.execute

    async def fail_ddl(self, sql, params=()):
        if sql.startswith("ALTER TABLE"):
            raise RuntimeError("disk full")
        await original_execute(self, sql, params)

    monkeypatch.setattr(FakeCursor, "execute", fail_ddl)
    with pytest.raises(RuntimeError, match="disk full"):
        await database.run_sql_migration(["ALTER TABLE existing_table ADD INDEX idx_new (id)"])
    assert any("RELEASE_LOCK" in sql for sql in server.statements)
