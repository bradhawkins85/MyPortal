"""Storage for RMM device stats, in the separate stats database
(:mod:`app.core.stats_database`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.stats_database import SAMPLES_TABLE, stats_db

_COLUMNS = ("company_id", "asset_id", "agent_id", "metric", "instance", "value", "sampled_at")


async def insert_samples(samples: list[dict[str, Any]]) -> int:
    """Store samples; one already stored for the same period is kept. Returns how many were new."""

    if not samples:
        return 0
    placeholders = ", ".join(["%s"] * len(_COLUMNS))
    return await stats_db.execute_many(
        f"INSERT IGNORE INTO {SAMPLES_TABLE} ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
        [tuple(sample.get(column) for column in _COLUMNS) for sample in samples],
    )


async def list_samples(*, asset_id: int, since: datetime) -> list[dict[str, Any]]:
    """A device's samples since ``since``, oldest first."""

    return await stats_db.fetch_all(
        f"SELECT metric, instance, value, sampled_at FROM {SAMPLES_TABLE} "  # nosec B608 - fixed table name
        "WHERE asset_id = %s AND sampled_at >= %s ORDER BY sampled_at, metric, instance",
        (int(asset_id), since),
    )


async def prune_samples(before: datetime, *, batch_size: int = 10000) -> int:
    """Delete samples taken before ``before``, in batches on MySQL."""

    if stats_db.is_sqlite:
        return await stats_db.execute_rowcount(f"DELETE FROM {SAMPLES_TABLE} WHERE sampled_at < %s", (before,))  # nosec B608
    removed = 0
    while True:
        count = await stats_db.execute_rowcount(
            f"DELETE FROM {SAMPLES_TABLE} WHERE sampled_at < %s LIMIT %s", (before, int(batch_size))  # nosec B608
        )
        removed += count
        if count < batch_size:
            return removed
