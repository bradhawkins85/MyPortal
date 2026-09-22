from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterable, Any
import types
import re
import hashlib
import json
import time

import aiosqlite
from loguru import logger

from .config import get_settings

try:
    import aiomysql
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-missing envs
    aiomysql = None  # type: ignore[assignment]
    _AIOMYSQL_IMPORT_ERROR = exc
else:
    _AIOMYSQL_IMPORT_ERROR = None


class Database:
    def __init__(self) -> None:
        self._pool: Any | None = None
        self._sqlite_conn: aiosqlite.Connection | None = None
        self._settings = get_settings()
        self._use_sqlite = self._should_use_sqlite()

    def _require_aiomysql(self) -> types.ModuleType:
        if aiomysql is None:
            raise RuntimeError(
                "MySQL support requires aiomysql; install project dependencies before running MySQL migrations."
            ) from _AIOMYSQL_IMPORT_ERROR
        return aiomysql

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
            mysql = self._require_aiomysql()
            logger.info("Connecting to MySQL at {host}", host=self._settings.database_host)
            self._pool = await mysql.create_pool(
                host=self._settings.database_host,
                port=self._settings.database_port,
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
                    adapted_sql, adapted_params = self._adapt_params_for_mysql(sql, params)
                    await cursor.execute(adapted_sql, adapted_params)

    async def execute_many(self, sql: str, params_seq: Iterable[tuple[Any, ...]]) -> None:
        seq = list(params_seq)
        if self._use_sqlite:
            if not self._sqlite_conn:
                raise RuntimeError("SQLite database not initialised")
            await self._sqlite_conn.executemany(sql, seq)
            await self._sqlite_conn.commit()
        else:
            async with self.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.executemany(sql, seq)

    async def execute_rowcount(self, sql: str, params: tuple | dict | None = None) -> int:
        if self._use_sqlite:
            if not self._sqlite_conn:
                raise RuntimeError("SQLite database not initialised")
            cursor = await self._sqlite_conn.execute(sql, params or ())
            await self._sqlite_conn.commit()
            return int(cursor.rowcount or 0)
        else:
            async with self.acquire() as conn:
                async with conn.cursor() as cursor:
                    adapted_sql, adapted_params = self._adapt_params_for_mysql(sql, params)
                    await cursor.execute(adapted_sql, adapted_params)
                    return int(cursor.rowcount or 0)

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
                    adapted_sql, adapted_params = self._adapt_params_for_mysql(sql, params)
                    await cursor.execute(adapted_sql, adapted_params)
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
            mysql = self._require_aiomysql()
            async with self.acquire() as conn:
                async with conn.cursor(mysql.DictCursor) as cursor:
                    adapted_sql, adapted_params = self._adapt_params_for_mysql(sql, params)
                    await cursor.execute(adapted_sql, adapted_params)
                    return await cursor.fetchone()

    async def fetch_many(self, sql: str, size: int, params: tuple | dict | None = None):
        """Execute ``sql`` and return at most ``size`` rows.

        Uses cursor-level ``fetchmany`` so only the requested number of rows is
        transferred from the database, avoiding the need to embed ``LIMIT``
        clauses (and thus user-controlled content) inside a wrapper SQL string.
        """
        if size <= 0:
            raise ValueError("size must be a positive integer")
        if self._use_sqlite:
            if not self._sqlite_conn:
                raise RuntimeError("SQLite database not initialised")
            cursor = await self._sqlite_conn.execute(sql, params or ())
            rows = await cursor.fetchmany(size)
            return [dict(row) for row in rows]
        else:
            mysql = self._require_aiomysql()
            async with self.acquire() as conn:
                async with conn.cursor(mysql.DictCursor) as cursor:
                    adapted_sql, adapted_params = self._adapt_params_for_mysql(sql, params)
                    await cursor.execute(adapted_sql, adapted_params)
                    return list(await cursor.fetchmany(size))

    async def fetch_all(self, sql: str, params: tuple | dict | None = None):
        if self._use_sqlite:
            if not self._sqlite_conn:
                raise RuntimeError("SQLite database not initialised")
            cursor = await self._sqlite_conn.execute(sql, params or ())
            rows = await cursor.fetchall()
            # Convert sqlite3.Row objects to dicts
            return [dict(row) for row in rows]
        else:
            mysql = self._require_aiomysql()
            async with self.acquire() as conn:
                async with conn.cursor(mysql.DictCursor) as cursor:
                    adapted_sql, adapted_params = self._adapt_params_for_mysql(sql, params)
                    await cursor.execute(adapted_sql, adapted_params)
                    return await cursor.fetchall()

    def _adapt_params_for_mysql(
        self, sql: str, params: tuple | list | dict | None
    ) -> tuple[str, tuple | list | dict | None]:
        """Translate SQLite-style positional (`?`) or named (`:name`) params to MySQL format."""

        if params is None:
            return sql, params

        if isinstance(params, dict):
            adapted_sql = re.sub(r":(\w+)", r"%(\1)s", sql)
            return adapted_sql, params

        # aiomysql uses the "format" paramstyle (`%s` placeholders). When a query
        # was authored with SQLite-style positional placeholders (`?`) and is
        # executed against MySQL, PyMySQL will attempt to apply `%` formatting to
        # the SQL string and raise ``TypeError: not all arguments converted during
        # string formatting`` because it cannot find any `%s` tokens. Replace
        # positional `?` placeholders with `%s` for MySQL while leaving existing
        # `%`-style placeholders untouched.
        if isinstance(params, (tuple, list)) and "?" in sql:
            placeholder_count = sql.count("?")
            if (
                placeholder_count == len(params)
                and "'" not in sql
                and '"' not in sql
                and "--" not in sql
                and "/*" not in sql
            ):
                # Fast path when placeholder count matches parameter count and no
                # obvious quotes or comments are present anywhere in the statement.
                # This intentionally errs on the side of safety and may skip the fast
                # path when these tokens appear inside identifiers or other text,
                # falling back to the slower but safer parser below.
                return sql.replace("?", "%s"), params

            # Only replace placeholders that are not inside quoted string literals.
            result_chars: list[str] = []
            in_single_quote = False
            in_double_quote = False
            in_line_comment = False
            in_block_comment = False
            length = len(sql)
            i = 0

            while i < length:
                char = sql[i]
                next_char = sql[i + 1] if i + 1 < length else ""

                if not in_single_quote and not in_double_quote and not in_block_comment:
                    if not in_line_comment and char == "-" and next_char == "-":
                        in_line_comment = True
                        result_chars.append(char)
                        result_chars.append(next_char)
                        i += 2
                        continue
                    if not in_line_comment and char == "/" and next_char == "*":
                        in_block_comment = True
                        result_chars.append(char)
                        result_chars.append(next_char)
                        i += 2
                        continue

                if in_line_comment:
                    result_chars.append(char)
                    i += 1
                    if char == "\n":
                        in_line_comment = False
                    continue

                if in_block_comment:
                    result_chars.append(char)
                    i += 1
                    if char == "*" and next_char == "/":
                        result_chars.append(next_char)
                        i += 1
                        in_block_comment = False
                    continue

                if (in_single_quote or in_double_quote) and char == "\\":
                    result_chars.append(char)
                    if next_char:
                        result_chars.append(next_char)
                        i += 2
                        continue
                    i += 1
                    continue

                if char == "'" and not in_double_quote:
                    result_chars.append(char)
                    if in_single_quote and next_char == "'":
                        # Doubled single quote representing a literal quote in SQL strings.
                        result_chars.append(next_char)
                        i += 2
                        continue
                    in_single_quote = not in_single_quote
                    i += 1
                    continue

                if char == '"' and not in_single_quote:
                    result_chars.append(char)
                    if in_double_quote and next_char == '"':
                        # Doubled double quote representing a literal quote in identifiers.
                        result_chars.append(next_char)
                        i += 2
                        continue
                    in_double_quote = not in_double_quote
                    i += 1
                    continue

                if char == "?" and not in_single_quote and not in_double_quote:
                    result_chars.append("%s")
                    i += 1
                    continue

                result_chars.append(char)
                i += 1

            return "".join(result_chars), params

        return sql, params

    def _get_migrations_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent / "migrations"

    async def _ensure_migrations_table(self, conn: Any) -> None:
        """Create migrations tracking table if it doesn't exist."""
        columns = (
            "name VARCHAR(255) PRIMARY KEY, checksum VARCHAR(64), "
            "state VARCHAR(16) NOT NULL DEFAULT 'completed', phase VARCHAR(16), "
            "started_at TEXT, completed_at TEXT, duration_ms INTEGER, error_details TEXT"
        )
        if self._use_sqlite:
            await conn.execute("CREATE TABLE IF NOT EXISTS migrations (" + columns + ")")
            cursor = await conn.execute("PRAGMA table_info(migrations)")
            present = {row[1] for row in await cursor.fetchall()}
            additions = {
                "checksum": "VARCHAR(64)", "state": "VARCHAR(16) NOT NULL DEFAULT 'completed'",
                "phase": "VARCHAR(16)", "started_at": "TEXT", "completed_at": "TEXT",
                "duration_ms": "INTEGER", "error_details": "TEXT",
            }
            for name, definition in additions.items():
                if name not in present:
                    await conn.execute("ALTER TABLE migrations ADD COLUMN " + name + " " + definition)
            await conn.commit()
        else:
            async with conn.cursor() as cursor:
                await cursor.execute("SET sql_notes = 0")
                try:
                    await cursor.execute(
                        "CREATE TABLE IF NOT EXISTS migrations (" + columns.replace("TEXT", "TEXT") + ")"
                    )
                    for name, definition in (
                        ("checksum", "VARCHAR(64) NULL"), ("state", "VARCHAR(16) NOT NULL DEFAULT 'completed'"),
                        ("phase", "VARCHAR(16) NULL"), ("started_at", "DATETIME(6) NULL"),
                        ("completed_at", "DATETIME(6) NULL"), ("duration_ms", "BIGINT NULL"),
                        ("error_details", "TEXT NULL"),
                    ):
                        await cursor.execute("ALTER TABLE migrations ADD COLUMN IF NOT EXISTS " + name + " " + definition)
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
        sql = re.sub(
            r'\bINTEGER\s+AUTOINCREMENT\s+PRIMARY\s+KEY\b',
            'INTEGER PRIMARY KEY AUTOINCREMENT',
            sql,
            flags=re.IGNORECASE,
        )
        
        # Replace DATETIME with TEXT (SQLite uses TEXT for dates)
        sql = re.sub(r'\bDATETIME\b', 'TEXT', sql, flags=re.IGNORECASE)
        
        # Remove ON UPDATE CURRENT_TIMESTAMP (not supported in SQLite)
        sql = re.sub(r'\s*ON\s+UPDATE\s+CURRENT_TIMESTAMP', '', sql, flags=re.IGNORECASE)
        
        # Parenthesised expressions are required for SQLite function defaults.
        sql = re.sub(r'\bCURRENT_TIMESTAMP\b', "(datetime('now'))", sql, flags=re.IGNORECASE)
        
        # Replace JSON column type with TEXT
        sql = re.sub(r'\bJSON\b', 'TEXT', sql, flags=re.IGNORECASE)

        # SQLite has no UUID() function. Generate canonical version-4 UUID text
        # for MySQL migrations that backfill public identifiers.
        sql = re.sub(
            r'\bUUID\(\)',
            "(lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || "
            "substr(lower(hex(randomblob(2))), 2) || '-' || "
            "substr('89ab', abs(random()) % 4 + 1, 1) || "
            "substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6))))",
            sql,
            flags=re.IGNORECASE,
        )

        # SQLite supports ADD COLUMN, but not MySQL's idempotency or placement
        # modifiers. Migration tracking ensures each file is only applied once.
        sql = re.sub(
            r'\bADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\b',
            'ADD COLUMN',
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(r'\s+AFTER\s+\w+(?=\s*;|\s*$)', '', sql, flags=re.IGNORECASE)

        # SQLite cannot replace an index inside ALTER TABLE. Convert the
        # MySQL form used when widening a uniqueness key into standalone
        # index statements so fallback databases retain the same invariant.
        sql = re.sub(
            r"ALTER\s+TABLE\s+(\w+)\s+DROP\s+INDEX\s+(\w+)\s*,\s*"
            r"ADD\s+UNIQUE\s+KEY\s+(\w+)\s*\(([^)]+)\)\s*;",
            lambda match: (
                f"DROP INDEX IF EXISTS {match.group(2)}; "
                f"CREATE UNIQUE INDEX {match.group(3)} ON {match.group(1)} "
                f"({match.group(4)});"
            ),
            sql,
            flags=re.IGNORECASE,
        )
        
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

    def _migration_metadata(self, path: Path) -> dict[str, Any]:
        """Read deployment compatibility metadata from leading SQL comments."""
        values: dict[str, str] = {}
        companion_metadata = False
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*--\s*(phase|compatible-from|compatible-to|maintenance)\s*:\s*(.*?)\s*$", line, re.I)
            if match:
                values[match.group(1).lower()] = match.group(2)
            elif line.strip() and not line.lstrip().startswith("--"):
                break
        # Migrations released before deployment metadata was introduced must
        # remain byte-for-byte stable: changing them would trip checksums on an
        # upgraded installation.  A companion manifest supplies metadata for
        # the recent, still-supported upgrade window without rewriting SQL that
        # may already have run.
        manifest_path = path.parent / "deployment_metadata.json"
        if not values and manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest_values = manifest.get("migrations", {}).get(path.name)
            except (json.JSONDecodeError, OSError) as exc:
                raise RuntimeError(f"Invalid migration metadata manifest {manifest_path.name}") from exc
            if manifest_values is not None:
                if not isinstance(manifest_values, dict):
                    raise RuntimeError(f"Invalid metadata for migration {path.name}")
                values = {str(key).lower(): str(value) for key, value in manifest_values.items()}
                companion_metadata = True
        phase = values.get("phase")
        # Keep the SQLite best-effort compatibility behaviour for immutable
        # pre-UPG02 SQL even when its deployment policy comes from the manifest.
        legacy_number = re.match(r"^([0-9]{1,3})_", path.name)
        legacy = bool(companion_metadata and legacy_number and int(legacy_number.group(1)) <= 382)
        if phase not in {"expand", "data", "backfill", "contract"}:
            # Existing migrations predate UPG02 and are grandfathered so a new
            # installation remains possible. New migrations must be declared.
            if legacy_number and int(legacy_number.group(1)) <= 382:
                phase = "expand"
                legacy = True
            else:
                raise RuntimeError(f"Migration {path.name} has no valid phase metadata")
        return {"phase": "data" if phase == "backfill" else phase, "legacy": legacy,
                "compatible_from": values.get("compatible-from"),
                "compatible_to": values.get("compatible-to"),
                "maintenance": values.get("maintenance", "false").lower() in {"1", "true", "yes"}}

    def _validate_migration_compatibility(self, path: Path, metadata: dict[str, Any],
                                          serving_release: str | None, target_release: str | None,
                                          maintenance: bool) -> None:
        if metadata["phase"] == "contract" and not metadata["maintenance"]:
            if not metadata["compatible_from"] or not metadata["compatible_to"]:
                raise RuntimeError(f"Contract migration {path.name} requires compatibility metadata")
        if metadata["maintenance"] and not maintenance:
            raise RuntimeError(f"Migration {path.name} requires UPG01 maintenance mode")
        for key, release in (("compatible_from", serving_release), ("compatible_to", target_release)):
            declared = metadata[key]
            if release and declared and declared != "*" and release not in {v.strip() for v in declared.split(",")}:
                raise RuntimeError(f"Migration {path.name} is not compatible with {release} ({key})")

    async def _apply_migration_file(self, conn: Any, path: Path, metadata: dict[str, Any] | None = None) -> None:
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
                    # Historical SQLite fallback migrations intentionally
                    # contained duplicate/MySQL-only statements. Preserve that
                    # bootstrap behaviour only for the frozen legacy set.
                    if not metadata or not metadata.get("legacy"):
                        raise
            if metadata is None:
                await conn.execute("INSERT INTO migrations (name) VALUES (?)", (path.name,))
            else:
                await conn.execute(
                    "UPDATE migrations SET state = ?, completed_at = CURRENT_TIMESTAMP, error_details = NULL WHERE name = ?",
                    ("completed", path.name),
                )
            await conn.commit()
        else:
            async with conn.cursor() as cursor:
                await cursor.execute("SET sql_notes = 0")
                try:
                    for statement in statements:
                        await cursor.execute(statement)
                finally:
                    await cursor.execute("SET sql_notes = 1")
                if metadata is None:
                    await cursor.execute("INSERT INTO migrations (name) VALUES (%s)", (path.name,))
                else:
                    await cursor.execute("UPDATE migrations SET state=%s, completed_at=UTC_TIMESTAMP(6), error_details=NULL WHERE name=%s", ("completed", path.name))

    async def run_migrations(self, *, serving_release: str | None = None,
                             target_release: str | None = None,
                             maintenance: bool = False) -> None:
        """Run all pending migrations."""
        # For MySQL, ensure database exists
        if not self._use_sqlite:
            mysql = self._require_aiomysql()
            database_name = self._settings.database_name or ""
            if not re.fullmatch(r"[A-Za-z0-9_]+", database_name):
                raise RuntimeError("Database name contains unsupported characters")
            temp_conn = await mysql.connect(
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
                        "CREATE DATABASE IF NOT EXISTS `" + database_name + "`"
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
                    cursor = await conn.execute("SELECT name, checksum, state FROM migrations")
                    applied_rows = await cursor.fetchall()
                    applied = {dict(row)["name"]: dict(row) for row in applied_rows}
                else:
                    mysql = self._require_aiomysql()
                    async with conn.cursor(mysql.DictCursor) as cursor:
                        await cursor.execute("SELECT name, checksum, state FROM migrations")
                        applied_rows = await cursor.fetchall()
                    applied = {row["name"]: row for row in applied_rows}

                # Apply pending migrations
                for path in sorted(migrations_dir.glob("*.sql")):
                    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                    previous = applied.get(path.name)
                    if previous and previous.get("state") == "completed":
                        if previous.get("checksum") and previous["checksum"] != checksum:
                            raise RuntimeError(f"Checksum mismatch for applied migration {path.name}")
                        # Backfill checksums for the grandfathered tracking rows.
                        if not previous.get("checksum"):
                            if self._use_sqlite:
                                await conn.execute("UPDATE migrations SET checksum=? WHERE name=?", (checksum, path.name)); await conn.commit()
                            else:
                                async with conn.cursor() as cursor: await cursor.execute("UPDATE migrations SET checksum=%s WHERE name=%s", (checksum, path.name))
                        continue
                    metadata = self._migration_metadata(path)
                    # A brand-new empty schema has no concurrently serving old
                    # release, so maintenance-only contract steps are safe as
                    # part of bootstrap. Upgrades must explicitly enter UPG01.
                    self._validate_migration_compatibility(
                        path, metadata, serving_release, target_release,
                        maintenance or not applied,
                    )
                    started = time.monotonic()
                    if self._use_sqlite:
                        await conn.execute("INSERT OR REPLACE INTO migrations (name,checksum,state,phase,started_at,error_details) VALUES (?,?,?,?,CURRENT_TIMESTAMP,NULL)", (path.name, checksum, "running", metadata["phase"])); await conn.commit()
                    else:
                        async with conn.cursor() as cursor:
                            await cursor.execute("INSERT INTO migrations (name,checksum,state,phase,started_at,error_details) VALUES (%s,%s,%s,%s,UTC_TIMESTAMP(6),NULL) ON DUPLICATE KEY UPDATE checksum=VALUES(checksum),state=VALUES(state),phase=VALUES(phase),started_at=VALUES(started_at),completed_at=NULL,error_details=NULL", (path.name, checksum, "running", metadata["phase"]))
                    try:
                        await self._apply_migration_file(conn, path, metadata)
                    except Exception as exc:
                        error = str(exc)[:4000]
                        duration = int((time.monotonic() - started) * 1000)
                        if self._use_sqlite:
                            await conn.execute("UPDATE migrations SET state=?,duration_ms=?,error_details=? WHERE name=?", ("failed", duration, error, path.name)); await conn.commit()
                        else:
                            async with conn.cursor() as cursor: await cursor.execute("UPDATE migrations SET state=%s,duration_ms=%s,error_details=%s WHERE name=%s", ("failed", duration, error, path.name))
                        raise RuntimeError(f"Migration {path.name} failed after partial DDL; inspect migrations.error_details, repair schema, then retry") from exc
                    duration = int((time.monotonic() - started) * 1000)
                    if self._use_sqlite:
                        await conn.execute("UPDATE migrations SET duration_ms=? WHERE name=?", (duration, path.name)); await conn.commit()
                    else:
                        async with conn.cursor() as cursor: await cursor.execute("UPDATE migrations SET duration_ms=%s WHERE name=%s", (duration, path.name))
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
db.connection = db.acquire  # Alias for backward compatibility
