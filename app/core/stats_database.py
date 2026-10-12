"""The secondary database that holds RMM device stats.

While a device has disk space, ping, CPU, memory or network checks, its RMM
agent reports those values every minute and MyPortal keeps one sample per
``STATS_SAMPLE_MINUTES`` for technicians to review on the asset page. The
samples live in their own MySQL/MariaDB database (``STATS_DB_*``), apart from
the main and event log databases, and are left out of backups unless
``STATS_DB_BACKUP`` is true.

Without ``STATS_DB_HOST``, ``_USER`` or ``_NAME`` checks still run but no
history is kept; nothing ever falls back to the main database. On connect
MyPortal creates the database when it is missing and the user may, then any
missing tables (:data:`MYSQL_SCHEMA`; see :mod:`app.core.secondary_database`).

Tests pass ``sqlite_path`` to use SQLite instead.
"""

from __future__ import annotations

from .config import get_settings
from .secondary_database import Connection, SecondaryDatabase

SAMPLES_TABLE = "rmm_device_stats"

# One row per device, metric, instance (drive, interface or ping target; empty
# for CPU and memory) and sample period. The unique key makes the first value
# in each period the one kept, so agents can report as often as they like.
MYSQL_SCHEMA: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS {SAMPLES_TABLE} (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      company_id INT NULL,
      asset_id INT NULL,
      agent_id INT NOT NULL,
      metric VARCHAR(32) NOT NULL,
      instance VARCHAR(255) NOT NULL DEFAULT '',
      value DOUBLE NOT NULL,
      sampled_at DATETIME NOT NULL,
      UNIQUE KEY uq_rmm_device_stats_sample (agent_id, metric, instance, sampled_at),
      KEY idx_rmm_device_stats_asset (asset_id, metric, sampled_at),
      KEY idx_rmm_device_stats_time (sampled_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
)

SQLITE_SCHEMA: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS {SAMPLES_TABLE} (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      company_id INTEGER NULL,
      asset_id INTEGER NULL,
      agent_id INTEGER NOT NULL,
      metric TEXT NOT NULL,
      instance TEXT NOT NULL DEFAULT '',
      value REAL NOT NULL,
      sampled_at TEXT NOT NULL,
      UNIQUE (agent_id, metric, instance, sampled_at)
    )
    """,
    f"CREATE INDEX IF NOT EXISTS idx_rmm_device_stats_asset ON {SAMPLES_TABLE} (asset_id, metric, sampled_at)",
    f"CREATE INDEX IF NOT EXISTS idx_rmm_device_stats_time ON {SAMPLES_TABLE} (sampled_at)",
)


def _connection() -> Connection:
    settings = get_settings()
    return Connection(
        host=settings.stats_database_host,
        port=settings.stats_database_port,
        user=settings.stats_database_user,
        password=settings.stats_database_password,
        name=settings.stats_database_name,
    )


class StatsDatabase(SecondaryDatabase):
    def __init__(self, *, sqlite_path: str | None = None) -> None:
        super().__init__(
            label="Stats",
            env_prefix="STATS_DB",
            settings=_connection,
            mysql_schema=MYSQL_SCHEMA,
            sqlite_schema=SQLITE_SCHEMA,
            sqlite_path=sqlite_path,
        )


stats_db = StatsDatabase()
