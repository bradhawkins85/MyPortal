"""The secondary database that holds RMM event logs.

Event logs are high volume and can be collected again, so they live in their
own MySQL/MariaDB database (``EVENT_LOG_DB_*``) instead of the main one, and
are left out of backups unless ``EVENT_LOG_DB_BACKUP`` is true.

Collection is off while ``EVENT_LOG_DB_HOST``, ``_USER`` or ``_NAME`` is empty:
nothing ever falls back to the main database. On connect MyPortal creates the
database when it is missing and the user may, then any missing tables
(:data:`MYSQL_SCHEMA`), columns (:data:`COLUMNS`) and indexes (:data:`INDEXES`). A connection failure never stops MyPortal starting;
it tries again at most once a minute (:mod:`app.core.secondary_database`).

Every device logs many of the same events, so storage is deflated two ways
(:mod:`app.services.rmm_event_logs`):

* message text is stored once in :data:`MESSAGES_TABLE`, keyed by its SHA-256,
  and entries point at it with ``message_id``. Entries stored before this
  keep their text in ``message`` until the daily prune moves it
  (:data:`META_TABLE` remembers how far it got);
* an event a device repeats within ``EVENT_LOG_REPEAT_WINDOW_MINUTES`` is kept
  as one entry: ``repeat_count`` occurrences from ``time_created`` (and
  ``record_id``) to ``last_time_created`` (and ``last_record_id``).

Tests pass ``sqlite_path`` to use SQLite instead.
"""

from __future__ import annotations

from .config import get_settings
from .secondary_database import Column, Connection, Index, SecondaryDatabase

ENTRIES_TABLE = "rmm_event_log_entries"
MESSAGES_TABLE = "rmm_event_log_messages"
META_TABLE = "rmm_event_log_meta"

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
      message_id BIGINT NULL,
      repeat_count INT NOT NULL DEFAULT 1,
      time_created DATETIME(6) NOT NULL,
      last_time_created DATETIME(6) NULL,
      last_record_id BIGINT NULL,
      received_at DATETIME NOT NULL,
      UNIQUE KEY uq_rmm_event_log_record (agent_id, log_name, record_id),
      KEY idx_rmm_event_log_company_time (company_id, time_created),
      KEY idx_rmm_event_log_asset_time (asset_id, log_name, time_created),
      KEY idx_rmm_event_log_company_asset_time (company_id, asset_id, time_created),
      KEY idx_rmm_event_log_time (time_created),
      KEY idx_rmm_event_log_repeats (agent_id, message_id, time_created)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {MESSAGES_TABLE} (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      message_hash BINARY(32) NOT NULL,
      message MEDIUMTEXT NOT NULL,
      last_seen_at DATETIME NOT NULL,
      UNIQUE KEY uq_rmm_event_log_message_hash (message_hash),
      KEY idx_rmm_event_log_message_seen (last_seen_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {META_TABLE} (
      name VARCHAR(64) NOT NULL PRIMARY KEY,
      value BIGINT NOT NULL
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
      message_id INTEGER NULL,
      repeat_count INTEGER NOT NULL DEFAULT 1,
      time_created TEXT NOT NULL,
      last_time_created TEXT NULL,
      last_record_id INTEGER NULL,
      received_at TEXT NOT NULL,
      UNIQUE (agent_id, log_name, record_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {MESSAGES_TABLE} (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      message_hash BLOB NOT NULL UNIQUE,
      message TEXT NOT NULL,
      last_seen_at TEXT NOT NULL
    )
    """,
    f"CREATE INDEX IF NOT EXISTS idx_rmm_event_log_message_seen ON {MESSAGES_TABLE} (last_seen_at)",
    f"CREATE TABLE IF NOT EXISTS {META_TABLE} (name TEXT NOT NULL PRIMARY KEY, value INTEGER NOT NULL)",
    f"CREATE INDEX IF NOT EXISTS idx_rmm_event_log_company_time ON {ENTRIES_TABLE} (company_id, time_created)",
    f"CREATE INDEX IF NOT EXISTS idx_rmm_event_log_asset_time ON {ENTRIES_TABLE} (asset_id, log_name, time_created)",
    f"CREATE INDEX IF NOT EXISTS idx_rmm_event_log_company_asset_time ON {ENTRIES_TABLE} (company_id, asset_id, time_created)",
    f"CREATE INDEX IF NOT EXISTS idx_rmm_event_log_time ON {ENTRIES_TABLE} (time_created)",
)

# Added to entries tables created before message de-duplication and repeat
# collapsing. New tables already have them.
COLUMNS: tuple[Column, ...] = (
    Column(ENTRIES_TABLE, "message_id", "BIGINT NULL"),
    Column(ENTRIES_TABLE, "repeat_count", "INT NOT NULL DEFAULT 1"),
    Column(ENTRIES_TABLE, "last_time_created", "DATETIME(6) NULL"),
    Column(ENTRIES_TABLE, "last_record_id", "BIGINT NULL"),
)
INDEXES: tuple[Index, ...] = (
    Index(ENTRIES_TABLE, "idx_rmm_event_log_repeats", "agent_id, message_id, time_created"),
    # The asset page reads a device's newest entries; without it MySQL walks
    # the whole company's history for a quiet device.
    Index(ENTRIES_TABLE, "idx_rmm_event_log_company_asset_time", "company_id, asset_id, time_created"),
)


def _connection() -> Connection:
    settings = get_settings()
    return Connection(
        host=settings.event_log_database_host,
        port=settings.event_log_database_port,
        user=settings.event_log_database_user,
        password=settings.event_log_database_password,
        name=settings.event_log_database_name,
    )


class EventLogDatabase(SecondaryDatabase):
    def __init__(self, *, sqlite_path: str | None = None) -> None:
        super().__init__(
            label="Event log",
            env_prefix="EVENT_LOG_DB",
            settings=_connection,
            mysql_schema=MYSQL_SCHEMA,
            sqlite_schema=SQLITE_SCHEMA,
            sqlite_path=sqlite_path,
            columns=COLUMNS,
            indexes=INDEXES,
        )


event_log_db = EventLogDatabase()
