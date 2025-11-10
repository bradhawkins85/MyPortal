from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterable, Any

import aiomysql
import aiosqlite
from loguru import logger

from .config import get_settings


class Database:
    def __init__(self) -> None:
        self._pool: aiomysql.Pool | None = None
        self._sqlite_conn: aiosqlite.Connection | None = None
        self._settings = get_settings()
        self._use_sqlite = self._should_use_sqlite()

    def _should_use_sqlite(self) -> bool:
        """Determine if SQLite should be used instead of MySQL.
        
        Returns True if any MySQL config is missing, False otherwise.
        """
        return not all([
            self._settings.database_host,
            self._settings.database_user,
            self._settings.database_name,
        ])
    
    def _get_sqlite_path(self) -> Path:
        """Get the path to the SQLite database file."""
        db_path = Path(__file__).resolve().parent.parent.parent / "myportal.db"
        return db_path
    
    def is_sqlite(self) -> bool:
        """Check if using SQLite instead of MySQL."""
        return self._use_sqlite

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
        if self._pool or self._sqlite_conn:
            return
        
        if self._use_sqlite:
            logger.info("Connecting to SQLite database")
            db_path = self._get_sqlite_path()
            self._sqlite_conn = await aiosqlite.connect(str(db_path))
            self._sqlite_conn.row_factory = aiosqlite.Row
            # Enable foreign keys in SQLite
            await self._sqlite_conn.execute("PRAGMA foreign_keys = ON")
            await self._sqlite_conn.commit()
        else:
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
        if self._sqlite_conn:
            logger.info("Disconnecting from SQLite database")
            await self._sqlite_conn.close()
            self._sqlite_conn = None
            logger.info("SQLite database disconnected successfully")
        elif self._pool:
            logger.info("Disconnecting from MySQL database")
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.info("MySQL database disconnected successfully")

    def is_connected(self) -> bool:
        return self._pool is not None or self._sqlite_conn is not None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        """Acquire a database connection.
        
        For MySQL, this returns a connection from the pool.
        For SQLite, this returns the single connection.
        """
        if self._use_sqlite:
            if not self._sqlite_conn:
                raise RuntimeError("SQLite database not initialised")
            yield self._sqlite_conn
        else:
            if not self._pool:
                raise RuntimeError("Database pool not initialised")
            conn = await self._pool.acquire()
            try:
                yield conn
            finally:
                self._pool.release(conn)

    async def execute(self, sql: str, params: tuple | dict | None = None) -> None:
        if self._use_sqlite:
            if not self._sqlite_conn:
                raise RuntimeError("SQLite database not initialised")
            await self._sqlite_conn.execute(sql, params or ())
            await self._sqlite_conn.commit()
        else:
            async with self.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(sql, params)

    async def execute_returning_lastrowid(
        self, sql: str, params: tuple | dict | None = None
    ) -> int:
        if self._use_sqlite:
            if not self._sqlite_conn:
                raise RuntimeError("SQLite database not initialised")
            cursor = await self._sqlite_conn.execute(sql, params or ())
            await self._sqlite_conn.commit()
            return cursor.lastrowid if cursor.lastrowid else 0
        else:
            async with self.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(sql, params)
                    last_row_id = cursor.lastrowid
            return int(last_row_id) if last_row_id is not None else 0

    async def fetch_one(self, sql: str, params: tuple | dict | None = None):
        if self._use_sqlite:
            if not self._sqlite_conn:
                raise RuntimeError("SQLite database not initialised")
            cursor = await self._sqlite_conn.execute(sql, params or ())
            row = await cursor.fetchone()
            # Convert sqlite3.Row to dict
            return dict(row) if row else None
        else:
            async with self.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(sql, params)
                    return await cursor.fetchone()

    async def fetch_all(self, sql: str, params: tuple | dict | None = None):
        if self._use_sqlite:
            if not self._sqlite_conn:
                raise RuntimeError("SQLite database not initialised")
            cursor = await self._sqlite_conn.execute(sql, params or ())
            rows = await cursor.fetchall()
            # Convert sqlite3.Row objects to dicts
            return [dict(row) for row in rows]
        else:
            async with self.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(sql, params)
                    return await cursor.fetchall()

    def _get_migrations_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent / "migrations"

    async def _ensure_migrations_table(self, conn: Any) -> None:
        """Create migrations tracking table if it doesn't exist."""
        if self._use_sqlite:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS migrations (name VARCHAR(255) PRIMARY KEY)"
            )
            await conn.commit()
        else:
            async with conn.cursor() as cursor:
                await cursor.execute("SET sql_notes = 0")
                try:
                    await cursor.execute(
                        "CREATE TABLE IF NOT EXISTS migrations (name VARCHAR(255) PRIMARY KEY)"
                    )
                finally:
                    await cursor.execute("SET sql_notes = 1")

    def _adapt_sql_for_sqlite(self, sql: str) -> str:
        """Adapt MySQL SQL to SQLite-compatible SQL.
        
        This handles basic MySQL-specific syntax that needs translation.
        For complex migrations, SQLite-specific versions may be needed.
        """
        # Remove MySQL-specific clauses
        import re
        
        # Remove ENGINE, CHARSET, COLLATE clauses
        sql = re.sub(r'\s*ENGINE\s*=\s*\w+', '', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\s*DEFAULT\s+CHARSET\s*=\s*\w+', '', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\s*COLLATE\s*=\s*\w+', '', sql, flags=re.IGNORECASE)
        
        # Replace AUTO_INCREMENT with AUTOINCREMENT
        sql = re.sub(r'\bAUTO_INCREMENT\b', 'AUTOINCREMENT', sql, flags=re.IGNORECASE)
        
        # Remove COMMENT clauses
        sql = re.sub(r'\s*COMMENT\s+\'[^\']*\'', '', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\s*COMMENT\s+"[^"]*"', '', sql, flags=re.IGNORECASE)
        
        # Replace INT with INTEGER for primary key autoincrement compatibility
        sql = re.sub(r'\bINT\b(\s+AUTOINCREMENT|\s+PRIMARY\s+KEY)', r'INTEGER\1', sql, flags=re.IGNORECASE)
        
        # Replace DATETIME with TEXT (SQLite uses TEXT for dates)
        sql = re.sub(r'\bDATETIME\b', 'TEXT', sql, flags=re.IGNORECASE)
        
        # Remove ON UPDATE CURRENT_TIMESTAMP (not supported in SQLite)
        sql = re.sub(r'\s*ON\s+UPDATE\s+CURRENT_TIMESTAMP', '', sql, flags=re.IGNORECASE)
        
        # Replace CURRENT_TIMESTAMP with datetime('now') for defaults
        sql = re.sub(r'\bCURRENT_TIMESTAMP\b', "datetime('now')", sql, flags=re.IGNORECASE)
        
        # Replace JSON column type with TEXT
        sql = re.sub(r'\bJSON\b', 'TEXT', sql, flags=re.IGNORECASE)
        
        # Handle ENUM types - convert to VARCHAR with CHECK constraint
        # This is a simplified approach; complex ENUMs may need manual handling
        enum_pattern = r"ENUM\s*\(([^)]+)\)"
        for match in re.finditer(enum_pattern, sql, flags=re.IGNORECASE):
            values = match.group(1)
            # Extract the column name before ENUM
            before_enum = sql[:match.start()]
            last_word_match = re.search(r'(\w+)\s*$', before_enum)
            if last_word_match:
                col_name = last_word_match.group(1)
                check_values = values.replace("'", "\"")
                check_constraint = f" CHECK ({col_name} IN ({check_values}))"
                sql = sql[:match.start()] + "VARCHAR(50)" + sql[match.end():]
                # Add CHECK constraint at the end of the column definition
                sql = sql.replace(f"{col_name} VARCHAR(50)", f"{col_name} VARCHAR(50){check_constraint}", 1)
        
        return sql

    async def _apply_migration_file(self, conn: Any, path: Path) -> None:
        """Apply a migration file to the database."""
        sql = path.read_text(encoding="utf-8")
        
        # Adapt SQL for SQLite if necessary
        if self._use_sqlite:
            sql = self._adapt_sql_for_sqlite(sql)
        
        statements = self._split_sql_statements(sql)
        
        if self._use_sqlite:
            for statement in statements:
                try:
                    await conn.execute(statement)
                except Exception as e:
                    logger.warning(
                        "Migration statement failed (may be MySQL-specific): {error}. Statement: {stmt}",
                        error=str(e),
                        stmt=statement[:100]
                    )
                    # Continue with other statements
            await conn.execute(
                "INSERT INTO migrations (name) VALUES (?)",
                (path.name,),
            )
            await conn.commit()
        else:
            async with conn.cursor() as cursor:
                for statement in statements:
                    await cursor.execute(statement)
                await cursor.execute(
                    "INSERT INTO migrations (name) VALUES (%s)",
                    (path.name,),
                )

    async def run_migrations(self) -> None:
        """Run all pending migrations."""
        # For MySQL, ensure database exists
        if not self._use_sqlite:
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

        lock_name = f"{self._settings.database_name or 'myportal'}_migration_lock"
        lock_timeout = getattr(self._settings, "migration_lock_timeout", 60)
        lock_acquired = False

        async with self.acquire() as conn:
            try:
                # Acquire lock (MySQL only, SQLite is single-threaded)
                if not self._use_sqlite:
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
                else:
                    lock_acquired = True  # SQLite doesn't need distributed locking

                await self._ensure_migrations_table(conn)

                # Get list of applied migrations
                if self._use_sqlite:
                    cursor = await conn.execute("SELECT name FROM migrations")
                    applied_rows = await cursor.fetchall()
                    applied = {dict(row)["name"] for row in applied_rows}
                else:
                    async with conn.cursor(aiomysql.DictCursor) as cursor:
                        await cursor.execute("SELECT name FROM migrations")
                        applied_rows = await cursor.fetchall()
                    applied = {row["name"] for row in applied_rows}

                # Apply pending migrations
                for path in sorted(migrations_dir.glob("*.sql")):
                    if path.name in applied:
                        continue
                    await self._apply_migration_file(conn, path)
                    logger.info("Applied migration {name}", name=path.name)
            finally:
                if lock_acquired and not self._use_sqlite:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))

    async def reprocess_migrations(self, names: Iterable[str] | None = None) -> None:
        """Reprocess specific migrations or all migrations."""
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

        lock_name = f"{self._settings.database_name or 'myportal'}_migration_lock"
        lock_timeout = getattr(self._settings, "migration_lock_timeout", 60)
        lock_acquired = False

        async with self.acquire() as conn:
            try:
                # Acquire lock (MySQL only)
                if not self._use_sqlite:
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
                else:
                    lock_acquired = True

                await self._ensure_migrations_table(conn)

                for path in target_paths:
                    if self._use_sqlite:
                        await conn.execute("DELETE FROM migrations WHERE name = ?", (path.name,))
                        await conn.commit()
                    else:
                        async with conn.cursor() as cursor:
                            await cursor.execute("DELETE FROM migrations WHERE name = %s", (path.name,))
                    await self._apply_migration_file(conn, path)
                    logger.info("Reprocessed migration {name}", name=path.name)
            finally:
                if lock_acquired and not self._use_sqlite:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))

    @asynccontextmanager
    async def acquire_lock(
        self,
        lock_name: str,
        timeout: int = 10,
    ) -> AsyncIterator[bool]:
        """Acquire a named database lock for distributed coordination.

        Args:
            lock_name: The name of the lock to acquire
            timeout: Maximum seconds to wait for the lock (default: 10)

        Yields:
            bool: True if lock was acquired, False otherwise

        For MySQL: Uses GET_LOCK() function for distributed locking across
        multiple workers/processes.
        
        For SQLite: Always returns True as SQLite is single-threaded and
        doesn't support distributed locking.
        
        When the database is not initialized, yields True to allow operations
        to proceed. This is for testing convenience and doesn't provide actual
        locking.
        """
        if self._use_sqlite:
            # SQLite is single-threaded, no need for distributed locking
            yield True
            return
            
        if not self._pool:
            # Database not initialized - likely in tests or early startup
            # Allow the operation to proceed without actual locking
            yield True
            return

        conn = await self._pool.acquire()
        lock_acquired = False
        try:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, timeout))
                result = await cursor.fetchone()
                lock_acquired = bool(result and result[0] == 1)

            yield lock_acquired
        finally:
            if lock_acquired:
                try:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                except Exception as exc:
                    # Log but don't raise - lock will be auto-released on connection close
                    # Defensive cleanup that shouldn't fail in normal operation
                    logger.warning(
                        "Failed to explicitly release lock {lock}: {error}",
                        lock=lock_name,
                        error=str(exc),
                    )
            self._pool.release(conn)


db = Database()

db = Database()
