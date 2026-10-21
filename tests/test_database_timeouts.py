"""Queries release scarce pool slots after stalls without replaying writes."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.database import Database, DatabaseQueryTimeout


class Connection:
    def __init__(self, statements):
        self.closed = False
        self.last_usage = asyncio.get_running_loop().time()
        self._reader = SimpleNamespace(
            at_eof=lambda: False, exception=lambda: None, eof_received=False,
        )
        self.statements = statements
        self.rowcount = 2
        self.lastrowid = 3
        self.sql = ""
        self.started = asyncio.Event()

    def close(self):
        self.closed = True

    def get_transaction_status(self):
        return False

    @asynccontextmanager
    async def cursor(self, *args):
        try:
            yield self
        finally:
            # Model cleanup that must not drain a stalled server response.
            if self.sql == "BLOCK" and not self.closed:
                await asyncio.Event().wait()

    async def execute(self, sql, params=None):
        self.sql = sql
        self.statements.append((sql, params))
        self.started.set()
        if sql == "BLOCK":
            await asyncio.Event().wait()
        if sql == "OTHER_TIMEOUT":
            raise TimeoutError("driver timeout")

    executemany = execute

    async def fetchone(self):
        if self.sql == "BLOCK_FETCH":
            await asyncio.Event().wait()
        return {"value": 1}

    async def fetchmany(self, size):
        return [await self.fetchone()]

    async def fetchall(self):
        return [await self.fetchone()]


@asynccontextmanager
async def connected_database():
    database = Database()
    database._use_sqlite = False
    database._settings = Settings(
        _env_file=None, DB_POOL_MIN_SIZE=0,
        DB_POOL_MAX_SIZE=1, DB_LOCK_POOL_MAX_SIZE=1,
    )
    database._settings.db_query_timeout = 0.02
    statements = []
    connections = []

    async def connect(**kwargs):
        connection = Connection(statements)
        connections.append(connection)
        return connection

    with patch("aiomysql.pool.connect", side_effect=connect):
        await database.connect()
        try:
            yield database, statements, connections
        finally:
            database._pool.terminate()
            database._lock_pool.terminate()
            await database.disconnect()


async def call_query(database, method, sql):
    if method == "execute_many":
        return await database.execute_many(sql, [(1,)])
    if method == "fetch_many":
        return await database.fetch_many(sql, 1, (1,))
    return await getattr(database, method)(sql, (1,))


@pytest.mark.parametrize("method", [
    "execute", "execute_many", "execute_rowcount", "execute_returning_lastrowid",
    "fetch_one", "fetch_many", "fetch_all",
])
def test_stalled_queries_discard_connection_without_retries(method):
    async def run():
        async with connected_database() as (database, statements, connections):
            with pytest.raises(DatabaseQueryTimeout):
                await asyncio.wait_for(call_query(database, method, "BLOCK"), 1)
            assert connections[0].closed
            assert statements == [("BLOCK", [(1,)] if method == "execute_many" else (1,))]
            await asyncio.wait_for(database.execute("SELECT 1"), 1)
            assert len(connections) == 2
            assert database._pool.freesize == 1

    asyncio.run(run())


@pytest.mark.parametrize("method", ["fetch_one", "fetch_many", "fetch_all"])
def test_deadline_includes_fetching_results(method):
    async def run():
        async with connected_database() as (database, statements, connections):
            with pytest.raises(DatabaseQueryTimeout):
                await asyncio.wait_for(call_query(database, method, "BLOCK_FETCH"), 1)
            assert connections[0].closed
            assert database._pool.size == 0

    asyncio.run(run())


def test_timed_out_query_wakes_an_existing_borrower():
    async def run():
        async with connected_database() as (database, statements, connections):
            owner = asyncio.create_task(database.execute("BLOCK"))
            while not connections:
                await asyncio.sleep(0)
            await connections[0].started.wait()
            waiter = asyncio.create_task(database.execute("SELECT 1"))
            while not database._pool._cond._waiters:
                await asyncio.sleep(0)
            with pytest.raises(DatabaseQueryTimeout):
                await owner
            await asyncio.wait_for(waiter, 1)
            assert database._pool.freesize == 1

    asyncio.run(asyncio.wait_for(run(), 2))


@pytest.mark.parametrize("method, expected", [
    ("execute", None), ("execute_many", None), ("execute_rowcount", 2),
    ("execute_returning_lastrowid", 3), ("fetch_one", {"value": 1}),
    ("fetch_many", [{"value": 1}]), ("fetch_all", [{"value": 1}]),
])
def test_successful_queries_preserve_results(method, expected):
    async def run():
        async with connected_database() as (database, statements, connections):
            assert await call_query(database, method, "SELECT ?") == expected
            assert statements[0][0] == ("SELECT ?" if method == "execute_many" else "SELECT %s")
            assert not connections[0].closed
            assert database._pool.freesize == 1

    asyncio.run(run())


def test_unrelated_timeout_keeps_original_type():
    async def run():
        async with connected_database() as (database, statements, connections):
            with pytest.raises(TimeoutError, match="driver timeout") as error:
                await database.execute("OTHER_TIMEOUT")
            assert type(error.value) is TimeoutError

    asyncio.run(run())


def test_concurrent_initialization_creates_only_one_pair_of_pools():
    async def run():
        database = Database()
        database._use_sqlite = False
        database._settings = Settings(_env_file=None, DB_POOL_MIN_SIZE=1)
        pools = []

        async def create_pool(**kwargs):
            await asyncio.sleep(0)
            pool = SimpleNamespace(
                close=lambda: None,
                wait_closed=lambda: asyncio.sleep(0),
            )
            pools.append((pool, kwargs))
            return pool

        with patch("app.core.database.aiomysql.create_pool", side_effect=create_pool):
            await asyncio.gather(*(database.connect() for _ in range(30)))
            assert len(pools) == 2
            assert database._pool is pools[0][0]
            assert database._lock_pool is pools[1][0]
            assert all(options["connect_timeout"] == 10 for _, options in pools)
            await database.disconnect()

    asyncio.run(run())


@pytest.mark.parametrize("key", ["DB_CONNECT_TIMEOUT", "DB_QUERY_TIMEOUT"])
@pytest.mark.parametrize("value", [0, -1, "nan", "inf"])
def test_invalid_database_timeouts_rejected(key, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{key: value})
