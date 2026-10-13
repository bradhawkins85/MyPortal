import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.database import Database


class Connection:
    def __init__(self, loop):
        self.closed = False
        self.last_usage = loop.time()
        self._reader = SimpleNamespace(
            at_eof=lambda: False, exception=lambda: None, eof_received=False
        )
        self.statements = []
        self.fail_release = False

    def close(self):
        self.closed = True

    def get_transaction_status(self):
        return False

    @asynccontextmanager
    async def cursor(self):
        yield self

    async def execute(self, sql, params=None):
        self.statements.append(sql)
        if self.fail_release and "RELEASE_LOCK" in sql:
            raise RuntimeError("lost lock response")

    async def fetchone(self):
        return (1,)


@asynccontextmanager
async def connected_database(query_size=1, lock_size=2):
    database = Database()
    database._settings = Settings(
        _env_file=None, DB_POOL_MIN_SIZE=0,
        DB_POOL_MAX_SIZE=query_size, DB_LOCK_POOL_MAX_SIZE=lock_size,
    )
    database._use_sqlite = False

    async def connect(**kwargs):
        return Connection(asyncio.get_running_loop())

    # Exercise real aiomysql pool scheduling; replace only the network connection.
    with patch("aiomysql.pool.connect", side_effect=connect):
        await database.connect()
        try:
            yield database
        finally:
            await database.disconnect()


def test_jobs_holding_all_lock_slots_can_still_query():
    async def run():
        async with connected_database() as database:
            async with AsyncExitStack() as stack:
                for index in range(2):
                    assert await stack.enter_async_context(database.acquire_lock(str(index)))
                assert database._lock_pool.freesize == 0
                await asyncio.wait_for(database.execute("SELECT 1"), timeout=1)
                assert database._pool.freesize == 1
            assert database._lock_pool.freesize == 2
    asyncio.run(run())


def test_query_pool_supports_more_than_ten_concurrent_connections():
    async def run():
        async with connected_database(query_size=50) as database:
            async with AsyncExitStack() as stack:
                connections = [
                    await asyncio.wait_for(stack.enter_async_context(database.acquire()), 1)
                    for _ in range(11)
                ]
                assert len(set(connections)) == 11
            assert database._pool.freesize == 11
    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_query_connection_returned_on_failure_or_cancellation(cancel):
    async def run():
        async with connected_database() as database:
            error = asyncio.CancelledError if cancel else RuntimeError
            with pytest.raises(error):
                async with database.acquire():
                    raise error()
            await asyncio.wait_for(database.execute("SELECT 1"), 1)
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["body", "cancel", "release"])
def test_lock_connections_with_uncertain_ownership_are_closed(failure):
    async def run():
        async with connected_database(lock_size=1) as database:
            error = asyncio.CancelledError if failure == "cancel" else RuntimeError
            try:
                async with database.acquire_lock("job"):
                    conn = next(iter(database._lock_pool._used))
                    if failure == "release":
                        conn.fail_release = True
                    else:
                        raise error()
            except error:
                if failure == "release":
                    raise
            assert conn.closed
            async with database.acquire_lock("next_job") as acquired:
                assert acquired
    asyncio.run(run())


def test_pool_configuration_and_shutdown():
    async def run():
        async with connected_database(query_size=17, lock_size=3) as database:
            query_pool, lock_pool = database._pool, database._lock_pool
            assert query_pool.maxsize == 17
            assert lock_pool.maxsize == 3
        assert query_pool.closed and lock_pool.closed
        assert database._pool is None and database._lock_pool is None
    asyncio.run(run())


def test_failed_lock_pool_startup_closes_query_pool():
    async def run():
        database = Database()
        database._use_sqlite = False
        pool = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
        with patch("app.core.database.aiomysql.create_pool", new=AsyncMock(side_effect=[pool, RuntimeError()])):
            with pytest.raises(RuntimeError):
                await database.connect()
        pool.wait_closed.assert_awaited_once()
        assert database._pool is None
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["query", "lock"])
def test_exhausted_pools_time_out_and_recover(kind):
    async def run():
        async with connected_database(lock_size=1) as database:
            database._settings.db_pool_wait_timeout = 0.02
            context = database.acquire if kind == "query" else lambda: database.acquire_lock("job")
            async with context():
                with pytest.raises(TimeoutError):
                    async with context():
                        pytest.fail("exhausted pool should not grant a connection")
                if kind == "lock":
                    await asyncio.wait_for(database.execute("SELECT 1"), 1)
            async with context():
                pass
    asyncio.run(run())


@pytest.mark.parametrize("values", [
    {"DB_POOL_MAX_SIZE": 0}, {"DB_LOCK_POOL_MAX_SIZE": 0},
    {"DB_POOL_MIN_SIZE": -1}, {"DB_POOL_MIN_SIZE": 5, "DB_POOL_MAX_SIZE": 4},
])
def test_invalid_pool_sizes_rejected(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
