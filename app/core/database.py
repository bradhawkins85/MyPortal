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

        await self.execute(
            "CREATE TABLE IF NOT EXISTS migrations (name VARCHAR(255) PRIMARY KEY)"
        )

        applied_rows = await self.fetch_all("SELECT name FROM migrations")
        applied = {row["name"] for row in applied_rows}

        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in applied:
                continue
            sql = path.read_text()
            statements = [s.strip() for s in sql.split(";") if s.strip()]
            async with self.acquire() as conn:
                async with conn.cursor() as cursor:
                    for statement in statements:
                        await cursor.execute(statement)
                    await cursor.execute(
                        "INSERT INTO migrations (name) VALUES (%s)", (path.name,)
                    )
            logger.info("Applied migration {name}", name=path.name)


db = Database()
