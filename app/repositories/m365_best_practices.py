"""Repository for Microsoft 365 Best Practices results and global settings."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
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
    notes: str | None = None,
    affected_accounts: list[dict[str, str]] | None = None,
    run_at: datetime,
) -> None:
    """Insert or update the latest result for a check for the given company.

    Manually-entered notes are preserved across re-evaluation runs and are only
    changed via ``update_result_notes``.
    """
    await db.execute(
        """
        INSERT INTO m365_best_practice_results
            (company_id, check_id, check_name, status, details, notes, affected_accounts, run_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            check_name = VALUES(check_name),
            status = VALUES(status),
            details = VALUES(details),
            notes = notes,
            affected_accounts = VALUES(affected_accounts),
            run_at = VALUES(run_at)
        """,
        (
            company_id,
            check_id,
            check_name,
            status,
            details,
            notes,
            json.dumps(affected_accounts or []),
            run_at,
        ),
    )
    await _upsert_daily_history(
        company_id=company_id,
        snapshot_date=run_at.date(),
        recorded_at=run_at,
    )


_SECURE_SCORE_PATTERN = re.compile(
    r"Secure Score is\s+([0-9]+(?:\.[0-9]+)?)/([0-9]+(?:\.[0-9]+)?)\s+\(([0-9]+(?:\.[0-9]+)?)%",
    re.IGNORECASE,
)


def _parse_secure_score(
    details: str | None,
) -> tuple[float | None, float | None, float | None]:
    """Extract the numeric Secure Score values emitted by the Graph check."""
    match = _SECURE_SCORE_PATTERN.search(details or "")
    if not match:
        return None, None, None
    return float(match.group(1)), float(match.group(2)), float(match.group(3))


async def _upsert_daily_history(
    *, company_id: int, snapshot_date: date, recorded_at: datetime
) -> None:
    """Store the current enabled-check totals as the company's UTC daily snapshot."""
    row = await db.fetch_one(
        """
        SELECT
            SUM(CASE WHEN r.status = 'pass' THEN 1 ELSE 0 END) AS pass_count,
            SUM(CASE WHEN r.status = 'fail' THEN 1 ELSE 0 END) AS fail_count,
            SUM(CASE WHEN r.status = 'unknown' THEN 1 ELSE 0 END) AS unknown_count,
            SUM(CASE WHEN r.status = 'not_applicable' THEN 1 ELSE 0 END) AS not_applicable_count,
            MAX(CASE WHEN r.check_id = 'bp_monitor_secure_score' THEN r.details END) AS secure_score_details
        FROM m365_best_practice_results r
        LEFT JOIN m365_best_practice_settings s ON s.check_id = r.check_id
        LEFT JOIN m365_best_practice_company_exclusions e
            ON e.company_id = r.company_id AND e.check_id = r.check_id
        WHERE r.company_id = %s
          AND (s.enabled = 1 OR s.check_id IS NULL)
          AND e.check_id IS NULL
        """,
        (company_id,),
    )
    values = dict(row or {})
    current_score, max_score, percentage = _parse_secure_score(
        values.get("secure_score_details")
    )
    await db.execute(
        """
        INSERT INTO m365_best_practice_daily_history
            (company_id, snapshot_date, pass_count, fail_count, unknown_count,
             not_applicable_count, secure_score, secure_score_max,
             secure_score_percentage, recorded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            pass_count = VALUES(pass_count),
            fail_count = VALUES(fail_count),
            unknown_count = VALUES(unknown_count),
            not_applicable_count = VALUES(not_applicable_count),
            secure_score = VALUES(secure_score),
            secure_score_max = VALUES(secure_score_max),
            secure_score_percentage = VALUES(secure_score_percentage),
            recorded_at = VALUES(recorded_at)
        """,
        (
            company_id,
            snapshot_date,
            int(values.get("pass_count") or 0),
            int(values.get("fail_count") or 0),
            int(values.get("unknown_count") or 0),
            int(values.get("not_applicable_count") or 0),
            current_score,
            max_score,
            percentage,
            recorded_at,
        ),
    )


async def list_daily_history(company_id: int, *, limit: int = 365) -> list[dict[str, Any]]:
    """Return up to one year of daily snapshots, newest first."""
    safe_limit = max(1, min(int(limit), 3650))
    rows = await db.fetch_all(
        """
        SELECT snapshot_date, pass_count, fail_count, unknown_count,
               not_applicable_count, secure_score, secure_score_max,
               secure_score_percentage, recorded_at
        FROM m365_best_practice_daily_history
        WHERE company_id = %s
        ORDER BY snapshot_date DESC
        LIMIT %s
        """,
        (company_id, safe_limit),
    )
    return [dict(row) for row in rows]


async def update_remediation_status(
    *,
    company_id: int,
    check_id: str,
    remediation_status: str,
    remediated_at: datetime,
) -> None:
    """Update the remediation status for an existing result row."""
    await db.execute(
        """
        UPDATE m365_best_practice_results
        SET remediation_status = %s, remediated_at = %s
        WHERE company_id = %s AND check_id = %s
        """,
        (remediation_status, remediated_at, company_id, check_id),
    )


async def list_results(company_id: int) -> list[dict[str, Any]]:
    """Return all stored best-practice results for a company."""
    rows = await db.fetch_all(
        """
        SELECT check_id, check_name, status, details, notes, affected_accounts, run_at,
               remediation_status, remediated_at
        FROM m365_best_practice_results
        WHERE company_id = %s
        ORDER BY check_id
        """,
        (company_id,),
    )
    results = []
    for row in rows:
        item = dict(row)
        try:
            item["affected_accounts"] = json.loads(item.get("affected_accounts") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            item["affected_accounts"] = []
        results.append(item)
    return results


async def update_result_notes(*, company_id: int, check_id: str, notes: str | None) -> None:
    """Persist the technician/admin note for one stored best-practice result."""
    await db.execute(
        """
        UPDATE m365_best_practice_results
        SET notes = %s
        WHERE company_id = %s AND check_id = %s
        """,
        (notes, company_id, check_id),
    )


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


async def delete_result_for_check_and_company(company_id: int, check_id: str) -> None:
    """Remove the stored result for a single check for a specific company."""
    await db.execute(
        "DELETE FROM m365_best_practice_results WHERE company_id = %s AND check_id = %s",
        (company_id, check_id),
    )


# ---------------------------------------------------------------------------
# Global enable/disable settings (super admin)
# ---------------------------------------------------------------------------


async def upsert_setting(*, check_id: str, enabled: bool, auto_remediate: bool = False) -> None:
    """Create or update the global enabled flag and auto-remediate flag for a single check."""
    await db.execute(
        """
        INSERT INTO m365_best_practice_settings (check_id, enabled, auto_remediate, updated_at)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            enabled = VALUES(enabled),
            auto_remediate = VALUES(auto_remediate),
            updated_at = VALUES(updated_at)
        """,
        (
            check_id,
            1 if enabled else 0,
            1 if auto_remediate else 0,
            datetime.now(timezone.utc).replace(tzinfo=None),
        ),
    )


async def list_settings() -> list[dict[str, Any]]:
    """Return all global best-practice settings rows."""
    rows = await db.fetch_all(
        """
        SELECT check_id, enabled, auto_remediate, updated_at
        FROM m365_best_practice_settings
        ORDER BY check_id
        """,
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        entry["enabled"] = bool(int(entry.get("enabled", 0) or 0))
        entry["auto_remediate"] = bool(int(entry.get("auto_remediate", 0) or 0))
        out.append(entry)
    return out


async def get_settings_map() -> dict[str, dict[str, bool]]:
    """Return a mapping of check_id → {enabled, auto_remediate} (both bool)."""
    rows = await list_settings()
    return {
        row["check_id"]: {
            "enabled": bool(row["enabled"]),
            "auto_remediate": bool(row["auto_remediate"]),
        }
        for row in rows
    }


# ---------------------------------------------------------------------------
# Per-company exclusions
# ---------------------------------------------------------------------------


async def get_company_exclusions(company_id: int) -> set[str]:
    """Return the set of check_ids excluded for a specific company."""
    rows = await db.fetch_all(
        """
        SELECT check_id
        FROM m365_best_practice_company_exclusions
        WHERE company_id = %s
        """,
        (company_id,),
    )
    return {row["check_id"] for row in rows}


async def set_company_exclusions(company_id: int, excluded_check_ids: set[str]) -> None:
    """Replace the full set of excluded check_ids for ``company_id``.

    Removes any exclusions no longer in the supplied set and inserts new ones.
    """
    await db.execute(
        "DELETE FROM m365_best_practice_company_exclusions WHERE company_id = %s",
        (company_id,),
    )
    for check_id in excluded_check_ids:
        await db.execute(
            """
            INSERT INTO m365_best_practice_company_exclusions (company_id, check_id)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE check_id = VALUES(check_id)
            """,
            (company_id, check_id),
        )


async def get_account_exclusions(company_id: int, check_id: str | None = None) -> set[tuple[str, str]]:
    """Return durable ``(check_id, account_id)`` exclusions for a company."""
    if check_id is None:
        rows = await db.fetch_all(
            "SELECT check_id, account_id FROM m365_best_practice_account_exclusions WHERE company_id = %s",
            (company_id,),
        )
    else:
        rows = await db.fetch_all(
            "SELECT check_id, account_id FROM m365_best_practice_account_exclusions WHERE company_id = %s AND check_id = %s",
            (company_id, check_id),
        )
    return {(str(row["check_id"]), str(row["account_id"])) for row in rows}


async def set_account_exclusion(
    *, company_id: int, check_id: str, account_id: str, account_name: str, excluded: bool
) -> None:
    """Add or remove one account exclusion without affecting other checks."""
    if excluded:
        await db.execute(
            """
            INSERT INTO m365_best_practice_account_exclusions
                (company_id, check_id, account_id, account_name, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE account_name = VALUES(account_name)
            """,
            (company_id, check_id, account_id, account_name,
             datetime.now(timezone.utc).replace(tzinfo=None)),
        )
    else:
        await db.execute(
            "DELETE FROM m365_best_practice_account_exclusions WHERE company_id = %s AND check_id = %s AND account_id = %s",
            (company_id, check_id, account_id),
        )
