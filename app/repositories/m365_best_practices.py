"""Repository for Microsoft 365 Best Practices results and global settings."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db


# ---------------------------------------------------------------------------
# Per-company results
# ---------------------------------------------------------------------------


async def upsert_result(
    *,
    company_id: int,
    check_id: str,
    check_name: str,
    status: str,
    details: str,
    run_at: datetime,
) -> None:
    """Insert or update the latest result for a check for the given company."""
    await db.execute(
        """
        INSERT INTO m365_best_practice_results
            (company_id, check_id, check_name, status, details, run_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            check_name = VALUES(check_name),
            status = VALUES(status),
            details = VALUES(details),
            run_at = VALUES(run_at)
        """,
        (company_id, check_id, check_name, status, details, run_at),
    )


async def list_results(company_id: int) -> list[dict[str, Any]]:
    """Return all stored best-practice results for a company."""
    rows = await db.fetch_all(
        """
        SELECT check_id, check_name, status, details, run_at
        FROM m365_best_practice_results
        WHERE company_id = %s
        ORDER BY check_id
        """,
        (company_id,),
    )
    return [dict(row) for row in rows]


async def delete_results(company_id: int) -> None:
    await db.execute(
        "DELETE FROM m365_best_practice_results WHERE company_id = %s",
        (company_id,),
    )


async def delete_result_for_check(check_id: str) -> None:
    """Remove all stored results for a single check across all companies."""
    await db.execute(
        "DELETE FROM m365_best_practice_results WHERE check_id = %s",
        (check_id,),
    )


# ---------------------------------------------------------------------------
# Global enable/disable settings (super admin)
# ---------------------------------------------------------------------------


async def upsert_setting(*, check_id: str, enabled: bool) -> None:
    """Create or update the global enabled flag for a single check."""
    await db.execute(
        """
        INSERT INTO m365_best_practice_settings (check_id, enabled, updated_at)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE enabled = VALUES(enabled), updated_at = VALUES(updated_at)
        """,
        (check_id, 1 if enabled else 0, datetime.utcnow()),
    )


async def list_settings() -> list[dict[str, Any]]:
    """Return all global best-practice settings rows."""
    rows = await db.fetch_all(
        """
        SELECT check_id, enabled, updated_at
        FROM m365_best_practice_settings
        ORDER BY check_id
        """,
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        entry["enabled"] = bool(int(entry.get("enabled", 0) or 0))
        out.append(entry)
    return out


async def get_settings_map() -> dict[str, bool]:
    """Return a mapping of check_id → enabled (bool)."""
    rows = await list_settings()
    return {row["check_id"]: bool(row["enabled"]) for row in rows}
