"""A dropped MySQL session must not strand borrowers already waiting for it."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.core.database import Database, DatabasePoolTimeout, DatabaseQueryTimeout


class Connection:
    def __init__(self):
        self.closed = False
        self.in_transaction = False
        self.last_usage = asyncio.get_running_loop().time()
        self._reader = SimpleNamespace(
            at_eof=lambda: False, exception=lambda: None, eof_received=False
        )
        self.query_started = asyncio.Event()
        self.get_lock_started = asyncio.Event()
        self.release_started = asyncio.Event()
        self.block_get_lock = False
        self.block_release = False
        self.fail_release = False

    def close(self):
        self.closed = True

    def get_transaction_status(self):
        return self.in_transaction

    @asynccontextmanager
    async def cursor(self, *args):
        yield self

    async def execute(self, sql, params=None):
        if "GET_LOCK" in sql and self.block_get_lock:
            self.get_lock_started.set()
            await asyncio.Event().wait()
        if "RELEASE_LOCK" in sql:
            if self.fail_release:
                raise RuntimeError("Lost MySQL connection while releasing lock")
            if self.block_release:
                self.release_started.set()
                await asyncio.Event().wait()
        if sql == "BLOCK":
            self.query_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # aiomysql closes the session when cancelled during packet I/O.
                self.close()
                raise

    async def fetchone(self):
        return (1,)


@asynccontextmanager
async def connected_database():
    database = Database()
    database._settings = Settings(
        _env_file=None, DB_POOL_MIN_SIZE=0,
        DB_POOL_MAX_SIZE=1, DB_LOCK_POOL_MAX_SIZE=1,
    )
    database._use_sqlite = False

    async def connect(**kwargs):
        return Connection()

    # Use real aiomysql scheduling and replace only network I/O.
    with patch("aiomysql.pool.connect", side_effect=connect):
        await database.connect()
        try:
            yield database
        finally:
            # Bound teardown even when a regression strands a used connection.
            for pool in (database._pool, database._lock_pool):
                if pool._used:
                    pool.terminate()
            await database.disconnect()


async def wait_for_borrower(pool):
    async def waiting():
        while not pool._cond._waiters:
            await asyncio.sleep(0)

    await asyncio.wait_for(waiting(), 1)


@pytest.mark.parametrize("failure", ["closed", "transaction"])
def test_query_pool_replaces_discarded_session_for_waiting_borrower(failure):
    async def run():
        async with connected_database() as database:
            async with database.acquire() as conn:
                waiter = asyncio.create_task(database.execute("SELECT 1"))
                await wait_for_borrower(database._pool)
                if failure == "closed":
                    conn.close()
                else:
                    conn.in_transaction = True
            await asyncio.wait_for(waiter, 1)
            assert conn.closed
            assert not database._pool._used
            assert database._pool.freesize == 1

    asyncio.run(run())


def test_hung_get_lock_discards_session_and_wakes_waiter():
    async def run():
        async with connected_database() as database:
            database._settings.db_query_timeout = 0.02
            async with database.acquire_lock("prime"):
                conn = next(iter(database._lock_pool._used))
            conn.block_get_lock = True

            async def borrow_lock(name):
                async with database.acquire_lock(name, timeout=0) as locked:
                    assert locked

            owner = asyncio.create_task(borrow_lock("hung"))
            await asyncio.wait_for(conn.get_lock_started.wait(), 1)
            waiter = asyncio.create_task(borrow_lock("waiter"))
            await wait_for_borrower(database._lock_pool)
            with pytest.raises(DatabaseQueryTimeout):
                await owner
            assert conn.closed
            await asyncio.wait_for(waiter, 1)
            assert database._lock_pool.freesize == 1

    asyncio.run(asyncio.wait_for(run(), 1))


def test_hung_release_lock_discards_session_and_wakes_waiter():
    async def run():
        async with connected_database() as database:
            database._settings.db_query_timeout = 0.02

            async def borrow_lock():
                async with database.acquire_lock("waiter", timeout=0) as locked:
                    assert locked

            async with database.acquire_lock("hung", timeout=0):
                conn = next(iter(database._lock_pool._used))
                conn.block_release = True
                waiter = asyncio.create_task(borrow_lock())
                await wait_for_borrower(database._lock_pool)
            assert conn.closed
            await asyncio.wait_for(waiter, 1)
            assert database._lock_pool.freesize == 1

    asyncio.run(asyncio.wait_for(run(), 1))


def test_lock_pool_wait_deadline_uses_database_timeout_type():
    async def run():
        async with connected_database() as database:
            database._settings.db_pool_wait_timeout = 0.02
            async with database.acquire_lock("owner"):
                with pytest.raises(DatabasePoolTimeout):
                    async with database.acquire_lock("waiter"):
                        pytest.fail("Exhausted pool granted a connection")

    asyncio.run(run())


def test_cancelled_query_wakes_existing_pool_waiter():
    async def run():
        async with connected_database() as database:
            owner = asyncio.create_task(database.execute("BLOCK"))
            async def query_acquired():
                while not database._pool._used:
                    await asyncio.sleep(0)

            await asyncio.wait_for(query_acquired(), 1)
            conn = next(iter(database._pool._used))
            await asyncio.wait_for(conn.query_started.wait(), 1)
            waiter = asyncio.create_task(database.execute("SELECT 1"))
            await wait_for_borrower(database._pool)
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            await asyncio.wait_for(waiter, 1)
            assert conn.closed
            assert database._pool.freesize == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["cancel", "release_error"])
def test_failed_lock_release_returns_session_and_wakes_waiter(failure):
    async def run():
        async with connected_database() as database:
            acquired = asyncio.Event()
            finish = asyncio.Event()

            async def hold_lock():
                async with database.acquire_lock("owner") as locked:
                    assert locked
                    acquired.set()
                    await finish.wait()

            async def borrow_lock():
                async with database.acquire_lock("waiter") as locked:
                    assert locked

            owner = asyncio.create_task(hold_lock())
            await asyncio.wait_for(acquired.wait(), 1)
            conn = next(iter(database._lock_pool._used))
            conn.block_release = failure == "cancel"
            conn.fail_release = failure == "release_error"
            waiter = asyncio.create_task(borrow_lock())
            await wait_for_borrower(database._lock_pool)
            finish.set()
            if failure == "cancel":
                await asyncio.wait_for(conn.release_started.wait(), 1)
                owner.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await owner
            else:
                await asyncio.wait_for(owner, 1)
            assert conn.closed
            assert conn not in database._lock_pool._used
            await asyncio.wait_for(waiter, 1)
            assert database._lock_pool.freesize == 1

    asyncio.run(run())
