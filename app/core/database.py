from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiomysql
from loguru import logger

from .config import get_settings


class Database:
    def __init__(self) -> None:
        self._pool: aiomysql.Pool | None = None
        self._settings = get_settings()

    async def connect(self) -> None:
        if self._pool:
            return
        logger.info("Connecting to MySQL at {host}", host=self._settings.database_host)
        self._pool = await aiomysql.create_pool(
            host=self._settings.database_host,
            user=self._settings.database_user,
            password=self._settings.database_password,
            db=self._settings.database_name,
            autocommit=True,
            minsize=1,
            maxsize=10,
            pool_recycle=600,
            init_command="SET time_zone = '+00:00'",
        )

    async def disconnect(self) -> None:
        if not self._pool:
            return
        self._pool.close()
        await self._pool.wait_closed()
        self._pool = None

    def is_connected(self) -> bool:
        return self._pool is not None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[aiomysql.Connection]:
        if not self._pool:
            raise RuntimeError("Database pool not initialised")
        conn = await self._pool.acquire()
        try:
            yield conn
        finally:
            self._pool.release(conn)

    async def execute(self, sql: str, params: tuple | dict | None = None) -> None:
        async with self.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)

    async def execute_returning_lastrowid(
        self, sql: str, params: tuple | dict | None = None
    ) -> int:
        async with self.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
                last_row_id = cursor.lastrowid
        return int(last_row_id) if last_row_id is not None else 0

    async def fetch_one(self, sql: str, params: tuple | dict | None = None):
        async with self.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchone()

    async def fetch_all(self, sql: str, params: tuple | dict | None = None):
        async with self.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchall()

    async def run_migrations(self) -> None:
        temp_conn = await aiomysql.connect(
            host=self._settings.database_host,
            user=self._settings.database_user,
            password=self._settings.database_password,
            autocommit=True,
            init_command="SET time_zone = '+00:00'",
        )
        async with temp_conn.cursor() as cursor:
            await cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{self._settings.database_name}`"
            )
        temp_conn.close()
        wait_closed = getattr(temp_conn, "wait_closed", None)
        if wait_closed:
            await wait_closed()

        await self.connect()
        migrations_dir = Path(__file__).resolve().parent.parent.parent / "migrations"
        if not migrations_dir.exists():
            logger.warning("No migrations directory found at {path}", path=str(migrations_dir))
            return

        lock_name = f"{self._settings.database_name}_migration_lock"
        lock_timeout = getattr(self._settings, "migration_lock_timeout", 60)
        lock_acquired = False

        async with self.acquire() as conn:
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, lock_timeout))
                    result = await cursor.fetchone()
                lock_acquired = bool(result and result[0] == 1)
                if not lock_acquired:
                    logger.error(
                        "Unable to obtain database migration lock {lock} within {timeout}s",
                        lock=lock_name,
                        timeout=lock_timeout,
                    )
                    raise RuntimeError("Could not obtain database migration lock")

                async with conn.cursor() as cursor:
                    await cursor.execute(
                        "CREATE TABLE IF NOT EXISTS migrations (name VARCHAR(255) PRIMARY KEY)"
                    )

                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute("SELECT name FROM migrations")
                    applied_rows = await cursor.fetchall()
                applied = {row["name"] for row in applied_rows}

                for path in sorted(migrations_dir.glob("*.sql")):
                    if path.name in applied:
                        continue
                    sql = path.read_text()
                    statements = [s.strip() for s in sql.split(";") if s.strip()]
                    async with conn.cursor() as cursor:
                        for statement in statements:
                            await cursor.execute(statement)
                        await cursor.execute(
                            "INSERT INTO migrations (name) VALUES (%s)", (path.name,)
                        )
                    logger.info("Applied migration {name}", name=path.name)
            finally:
                if lock_acquired:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))


db = Database()
