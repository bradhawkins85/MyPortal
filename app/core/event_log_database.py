"""The secondary database that holds RMM event logs.

Event logs are high volume and can be collected again, so they live in their
own MySQL/MariaDB database (``EVENT_LOG_DB_*``) instead of the main one, and
are left out of backups unless ``EVENT_LOG_DB_BACKUP`` is true.

Collection is off while ``EVENT_LOG_DB_HOST``, ``_USER`` or ``_NAME`` is empty:
nothing ever falls back to the main database. On connect MyPortal creates the
database when it is missing and the user may, then any missing tables
(:data:`MYSQL_SCHEMA`). A connection failure never stops MyPortal starting;
:meth:`EventLogDatabase.ensure_connected` tries again at most once a minute.

Tests pass ``sqlite_path`` to use SQLite instead.
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Iterable

import aiosqlite
from loguru import logger

from .config import get_settings

try:
    import aiomysql
except ModuleNotFoundError:  # pragma: no cover - exercised in dependency-missing envs
    aiomysql = None  # type: ignore[assignment]

ENTRIES_TABLE = "rmm_event_log_entries"

# Every table the event log database needs. CREATE TABLE IF NOT EXISTS keeps
# connecting idempotent; indexes are inline because MySQL has no
# CREATE INDEX IF NOT EXISTS.
MYSQL_SCHEMA: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS {ENTRIES_TABLE} (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      company_id INT NULL,
      asset_id INT NULL,
      agent_id INT NOT NULL,
      log_name VARCHAR(255) NOT NULL,
      record_id BIGINT NOT NULL,
      event_id INT NOT NULL,
      level TINYINT NOT NULL DEFAULT 4,
      provider VARCHAR(255) NULL,
      computer VARCHAR(255) NULL,
      message MEDIUMTEXT NULL,
      time_created DATETIME(6) NOT NULL,
      received_at DATETIME NOT NULL,
      UNIQUE KEY uq_rmm_event_log_record (agent_id, log_name, record_id),
      KEY idx_rmm_event_log_company_time (company_id, time_created),
      KEY idx_rmm_event_log_asset_time (asset_id, log_name, time_created),
      KEY idx_rmm_event_log_time (time_created)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
)

SQLITE_SCHEMA: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS {ENTRIES_TABLE} (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      company_id INTEGER NULL,
      asset_id INTEGER NULL,
      agent_id INTEGER NOT NULL,
      log_name TEXT NOT NULL,
      record_id INTEGER NOT NULL,
      event_id INTEGER NOT NULL,
      level INTEGER NOT NULL DEFAULT 4,
      provider TEXT NULL,
      computer TEXT NULL,
      message TEXT NULL,
      time_created TEXT NOT NULL,
      received_at TEXT NOT NULL,
      UNIQUE (agent_id, log_name, record_id)
    )
    """,
    f"CREATE INDEX IF NOT EXISTS idx_rmm_event_log_company_time ON {ENTRIES_TABLE} (company_id, time_created)",
    f"CREATE INDEX IF NOT EXISTS idx_rmm_event_log_asset_time ON {ENTRIES_TABLE} (asset_id, log_name, time_created)",
    f"CREATE INDEX IF NOT EXISTS idx_rmm_event_log_time ON {ENTRIES_TABLE} (time_created)",
)

_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_$]{1,64}$")
_UNKNOWN_DATABASE = 1049
RETRY_SECONDS = 60


class EventLogDatabase:
    def __init__(self, *, sqlite_path: str | None = None) -> None:
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
        settings = get_settings()
        return all([
            settings.event_log_database_host,
            settings.event_log_database_user,
            settings.event_log_database_name,
        ])

    def is_connected(self) -> bool:
        return self._pool is not None or self._sqlite_conn is not None

    def status(self) -> dict[str, Any]:
        """What the Event logs page shows about the database."""

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
                    for statement in SQLITE_SCHEMA:
                        await conn.execute(statement)
                    await conn.commit()
                    self._sqlite_conn = conn
                else:
                    self._pool = await self._create_mysql_pool()
                    async with self._pool.acquire() as conn:
                        async with conn.cursor() as cursor:
                            for statement in MYSQL_SCHEMA:
                                await cursor.execute(statement)
            except Exception as exc:
                await self._close()
                self.last_error = str(exc) or exc.__class__.__name__
                logger.error("Event log database unavailable: {error}", error=self.last_error)
                return False
            self.last_error = None
            logger.info("Event log database connected")
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
        settings = get_settings()
        name = str(settings.event_log_database_name)
        if not _DATABASE_NAME.match(name):
            raise ValueError("EVENT_LOG_DB_NAME may only contain letters, digits, _ and $")
        options = dict(
            host=settings.event_log_database_host,
            port=settings.event_log_database_port,
            user=settings.event_log_database_user,
            password=settings.event_log_database_password or "",
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
        logger.info("Created event log database {name}", name=name)
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
        return sql.replace("%s", "?").replace("INSERT IGNORE", "INSERT OR IGNORE")

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
            raise RuntimeError("Event log database not connected")
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


event_log_db = EventLogDatabase()
