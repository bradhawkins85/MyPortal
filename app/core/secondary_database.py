"""A secondary MySQL/MariaDB database MyPortal keeps apart from the main one.

High-volume data that can be collected again (RMM event logs, RMM device
stats) lives in its own database so the main database stays small and backups
stay quick. Each one is configured with its own ``<PREFIX>_DB_*`` settings and
is off while host, user or name is empty: nothing ever falls back to the main
database. On connect MyPortal creates the database when it is missing and the
user may, then any missing tables. A connection failure never stops MyPortal
starting; :meth:`SecondaryDatabase.ensure_connected` tries again at most once
a minute.

Tests pass ``sqlite_path`` to use SQLite instead.
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Iterable

import aiosqlite
from loguru import logger

try:
    import aiomysql
except ModuleNotFoundError:  # pragma: no cover - exercised in dependency-missing envs
    aiomysql = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Connection:
    host: str | None
    port: int
    user: str | None
    password: str | None
    name: str | None


_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_$]{1,64}$")
_UNKNOWN_DATABASE = 1049
RETRY_SECONDS = 60


class SecondaryDatabase:
    """``label`` names the database in logs; ``env_prefix`` is its settings'
    prefix (``EVENT_LOG_DB``); ``settings`` reads them."""

    def __init__(
        self,
        *,
        label: str,
        env_prefix: str,
        settings: Callable[[], Connection],
        mysql_schema: tuple[str, ...],
        sqlite_schema: tuple[str, ...],
        sqlite_path: str | None = None,
    ) -> None:
        self.label = label
        self.env_prefix = env_prefix
        self._settings = settings
        self._mysql_schema = mysql_schema
        self._sqlite_schema = sqlite_schema
        self._sqlite_path = sqlite_path
        self._pool: Any | None = None
        self._sqlite_conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._last_attempt = 0.0
        self.last_error: str | None = None

    @property
    def is_sqlite(self) -> bool:
        return self._sqlite_path is not None

    def is_configured(self) -> bool:
        if self.is_sqlite:
            return True
        settings = self._settings()
        return all([settings.host, settings.user, settings.name])

    def is_connected(self) -> bool:
        return self._pool is not None or self._sqlite_conn is not None

    def status(self) -> dict[str, Any]:
        """What the pages using the database show about it."""

        return {
            "configured": self.is_configured(),
            "connected": self.is_connected(),
            "error": self.last_error,
        }

    async def connect(self) -> bool:
        """Connect and create anything missing. Returns False, and logs why,
        when collection is off or the database cannot be reached."""

        async with self._lock:
            if self.is_connected():
                return True
            if not self.is_configured():
                return False
            self._last_attempt = time.monotonic()
            try:
                if self.is_sqlite:
                    conn = await aiosqlite.connect(self._sqlite_path)
                    conn.row_factory = aiosqlite.Row
                    for statement in self._sqlite_schema:
                        await conn.execute(statement)
                    await conn.commit()
                    self._sqlite_conn = conn
                else:
                    self._pool = await self._create_mysql_pool()
                    async with self._pool.acquire() as conn:
                        async with conn.cursor() as cursor:
                            for statement in self._mysql_schema:
                                await cursor.execute(statement)
            except Exception as exc:
                await self._close()
                self.last_error = str(exc) or exc.__class__.__name__
                logger.error("{label} database unavailable: {error}", label=self.label, error=self.last_error)
                return False
            self.last_error = None
            logger.info("{label} database connected", label=self.label)
            return True

    async def ensure_connected(self) -> bool:
        """Connect when not yet connected, trying at most once a minute."""

        if self.is_connected():
            return True
        if not self.is_configured():
            return False
        if self._last_attempt and time.monotonic() - self._last_attempt < RETRY_SECONDS:
            return False
        return await self.connect()

    async def _create_mysql_pool(self) -> Any:
        if aiomysql is None:
            raise RuntimeError("MySQL support requires aiomysql")
        settings = self._settings()
        name = str(settings.name)
        if not _DATABASE_NAME.match(name):
            raise ValueError(f"{self.env_prefix}_NAME may only contain letters, digits, _ and $")
        options = dict(
            host=settings.host,
            port=settings.port,
            user=settings.user,
            password=settings.password or "",
            autocommit=True,
            init_command="SET time_zone = '+00:00'",
        )
        try:
            return await aiomysql.create_pool(db=name, minsize=1, maxsize=5, pool_recycle=600, **options)
        except aiomysql.OperationalError as exc:
            if not exc.args or exc.args[0] != _UNKNOWN_DATABASE:
                raise
        # The database is missing: create it (the name was checked above).
        conn = await aiomysql.connect(**options)
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        finally:
            conn.close()
        logger.info("Created {label} database {name}", label=self.label, name=name)
        return await aiomysql.create_pool(db=name, minsize=1, maxsize=5, pool_recycle=600, **options)

    async def _close(self) -> None:
        if self._sqlite_conn is not None:
            await self._sqlite_conn.close()
            self._sqlite_conn = None
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def disconnect(self) -> None:
        async with self._lock:
            await self._close()

    def _sql(self, sql: str) -> str:
        if not self.is_sqlite:
            return sql
        sql = sql.replace("%s", "?")
        sql = sql.replace("INSERT IGNORE", "INSERT OR IGNORE")
        # Convert MySQL "ON DUPLICATE KEY UPDATE id = id" to a plain SQLite
        # INSERT OR IGNORE (the no-op update has no effect; OR IGNORE does
        # the same skip-on-conflict).
        if "ON DUPLICATE KEY UPDATE id = id" in sql:
            sql = sql.replace(" ON DUPLICATE KEY UPDATE id = id", "")
            sql = re.sub(r"\bINSERT INTO\b", "INSERT OR IGNORE INTO", sql, count=1)
        return sql

    @staticmethod
    def _sqlite_params(params: Iterable[Any]) -> tuple:
        # SQLite stores dates as text that sorts in time order.
        return tuple(
            value.strftime("%Y-%m-%d %H:%M:%S.%f") if isinstance(value, datetime) else value for value in params
        )

    @asynccontextmanager
    async def _cursor(self) -> AsyncIterator[Any]:
        if self._sqlite_conn is not None:
            yield None
            return
        if self._pool is None:
            raise RuntimeError(f"{self.label} database not connected")
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                yield cursor

    async def execute_rowcount(self, sql: str, params: tuple | list = ()) -> int:
        async with self._cursor() as cursor:
            if cursor is None:
                result = await self._sqlite_conn.execute(self._sql(sql), self._sqlite_params(params))  # type: ignore[union-attr]
                await self._sqlite_conn.commit()  # type: ignore[union-attr]
                return int(result.rowcount or 0)
            await cursor.execute(sql, params)
            return int(cursor.rowcount or 0)

    async def execute_many(self, sql: str, params_seq: Iterable[tuple]) -> int:
        """Run ``sql`` for each parameter tuple; returns the rows changed."""

        seq = list(params_seq)
        if not seq:
            return 0
        async with self._cursor() as cursor:
            if cursor is None:
                before = self._sqlite_conn.total_changes  # type: ignore[union-attr]
                await self._sqlite_conn.executemany(self._sql(sql), [self._sqlite_params(row) for row in seq])  # type: ignore[union-attr]
                await self._sqlite_conn.commit()  # type: ignore[union-attr]
                return self._sqlite_conn.total_changes - before  # type: ignore[union-attr]
            await cursor.executemany(sql, seq)
            return int(cursor.rowcount or 0)

    async def fetch_all(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        async with self._cursor() as cursor:
            if cursor is None:
                result = await self._sqlite_conn.execute(self._sql(sql), self._sqlite_params(params))  # type: ignore[union-attr]
                return [dict(row) for row in await result.fetchall()]
            await cursor.execute(sql, params)
            return [dict(row) for row in await cursor.fetchall()]

