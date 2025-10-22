from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterable

import aiomysql
from loguru import logger

from .config import get_settings


class Database:
    def __init__(self) -> None:
        self._pool: aiomysql.Pool | None = None
        self._settings = get_settings()

    def _split_sql_statements(self, sql: str) -> list[str]:
        """Split raw SQL script content into executable statements.

        The migration runner historically split files on semicolons directly,
        which breaks when statements contain literal semicolons inside quoted
        strings (for example HTML content or JSON snippets).  This parser keeps
        track of quote and comment state so delimiters inside literals do not
        prematurely terminate a statement.
        """

        statements: list[str] = []
        statement_chars: list[str] = []
        in_single_quote = False
        in_double_quote = False
        i = 0
        length = len(sql)

        while i < length:
            char = sql[i]
            next_char = sql[i + 1] if i + 1 < length else ""

            if not in_single_quote and not in_double_quote:
                if char == "-" and next_char == "-":
                    i += 2
                    while i < length and sql[i] != "\n":
                        i += 1
                    continue
                if char == "/" and next_char == "*":
                    i += 2
                    while i + 1 < length and not (sql[i] == "*" and sql[i + 1] == "/"):
                        i += 1
                    i += 2
                    continue

            if char == "'" and not in_double_quote:
                statement_chars.append(char)
                if in_single_quote:
                    if next_char == "'":
                        statement_chars.append(next_char)
                        i += 2
                        continue
                    in_single_quote = False
                else:
                    in_single_quote = True
                i += 1
                continue

            if char == '"' and not in_single_quote:
                statement_chars.append(char)
                if in_double_quote:
                    if next_char == '"':
                        statement_chars.append(next_char)
                        i += 2
                        continue
                    in_double_quote = False
                else:
                    in_double_quote = True
                i += 1
                continue

            if char == ";" and not in_single_quote and not in_double_quote:
                statement = "".join(statement_chars).strip()
                if statement:
                    statements.append(statement)
                statement_chars = []
                i += 1
                continue

            statement_chars.append(char)
            i += 1

        remaining = "".join(statement_chars).strip()
        if remaining:
            statements.append(remaining)
        return statements

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

    def _get_migrations_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent / "migrations"

    async def _ensure_migrations_table(self, conn: aiomysql.Connection) -> None:
        async with conn.cursor() as cursor:
            await cursor.execute("SET sql_notes = 0")
            try:
                await cursor.execute(
                    "CREATE TABLE IF NOT EXISTS migrations (name VARCHAR(255) PRIMARY KEY)"
                )
            finally:
                await cursor.execute("SET sql_notes = 1")

    async def _apply_migration_file(self, conn: aiomysql.Connection, path: Path) -> None:
        sql = path.read_text(encoding="utf-8")
        statements = self._split_sql_statements(sql)
        async with conn.cursor() as cursor:
            for statement in statements:
                await cursor.execute(statement)
            await cursor.execute(
                "INSERT INTO migrations (name) VALUES (%s)",
                (path.name,),
            )

    async def run_migrations(self) -> None:
        temp_conn = await aiomysql.connect(
            host=self._settings.database_host,
            user=self._settings.database_user,
            password=self._settings.database_password,
            autocommit=True,
            init_command="SET time_zone = '+00:00'",
        )
        async with temp_conn.cursor() as cursor:
            await cursor.execute("SET sql_notes = 0")
            try:
                await cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self._settings.database_name}`"
                )
            finally:
                await cursor.execute("SET sql_notes = 1")
        temp_conn.close()
        wait_closed = getattr(temp_conn, "wait_closed", None)
        if wait_closed:
            await wait_closed()

        await self.connect()
        migrations_dir = self._get_migrations_dir()
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

                await self._ensure_migrations_table(conn)

                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute("SELECT name FROM migrations")
                    applied_rows = await cursor.fetchall()
                applied = {row["name"] for row in applied_rows}

                for path in sorted(migrations_dir.glob("*.sql")):
                    if path.name in applied:
                        continue
                    await self._apply_migration_file(conn, path)
                    logger.info("Applied migration {name}", name=path.name)
            finally:
                if lock_acquired:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))

    async def reprocess_migrations(self, names: Iterable[str] | None = None) -> None:
        await self.connect()
        migrations_dir = self._get_migrations_dir()
        if not migrations_dir.exists():
            logger.warning("No migrations directory found at {path}", path=str(migrations_dir))
            return

        available = {path.name: path for path in sorted(migrations_dir.glob("*.sql"))}
        if not available:
            logger.info("No migrations available to reprocess in {path}", path=str(migrations_dir))
            return

        if names is None:
            target_paths = list(available.values())
        else:
            normalised = []
            for name in names:
                if not name:
                    continue
                candidate = name if name.endswith(".sql") else f"{name}.sql"
                normalised.append(candidate)

            deduped: list[str] = []
            seen: set[str] = set()
            for name in normalised:
                if name in seen:
                    continue
                seen.add(name)
                deduped.append(name)
            normalised = deduped

            missing = [name for name in normalised if name not in available]
            if missing:
                raise ValueError(
                    "Unknown migrations requested for reprocessing: " + ", ".join(sorted(missing))
                )

            target_paths = [available[name] for name in normalised]

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

                await self._ensure_migrations_table(conn)

                for path in target_paths:
                    async with conn.cursor() as cursor:
                        await cursor.execute("DELETE FROM migrations WHERE name = %s", (path.name,))
                    await self._apply_migration_file(conn, path)
                    logger.info("Reprocessed migration {name}", name=path.name)
            finally:
                if lock_acquired:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))


db = Database()
