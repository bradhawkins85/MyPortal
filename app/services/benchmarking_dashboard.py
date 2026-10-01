from __future__ import annotations

from typing import Any

from app.core.database import db


async def build_cross_company_summary(*, limit: int = 250) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            c.id AS company_id,
            c.name AS company_name,
            h.snapshot_date,
            h.pass_count,
            h.fail_count,
            h.unknown_count,
            h.not_applicable_count,
            h.secure_score_percentage
        FROM companies AS c
        INNER JOIN (
            SELECT company_id, MAX(snapshot_date) AS snapshot_date
            FROM m365_best_practice_daily_history
            GROUP BY company_id
        ) latest
            ON latest.company_id = c.id
        INNER JOIN m365_best_practice_daily_history AS h
            ON h.company_id = latest.company_id
           AND h.snapshot_date = latest.snapshot_date
        WHERE COALESCE(c.archived, 0) = 0
        ORDER BY COALESCE(h.secure_score_percentage, 0) DESC, c.name ASC
        LIMIT %s
        """,
        (max(1, min(int(limit), 1000)),),
    )
    summary: list[dict[str, Any]] = []
    for row in rows or []:
        pass_count = int(row.get("pass_count") or 0)
        fail_count = int(row.get("fail_count") or 0)
        unknown_count = int(row.get("unknown_count") or 0)
        not_applicable_count = int(row.get("not_applicable_count") or 0)
        rated_total = max(pass_count + fail_count, 0)
        pass_percentage = round((pass_count / rated_total * 100.0), 1) if rated_total else 0.0
        summary.append(
            {
                "company_id": int(row.get("company_id") or 0),
                "company_name": row.get("company_name") or "Unknown",
                "snapshot_date": row.get("snapshot_date"),
                "pass_count": pass_count,
                "fail_count": fail_count,
                "unknown_count": unknown_count,
                "not_applicable_count": not_applicable_count,
                "rated_total": rated_total,
                "pass_percentage": pass_percentage,
                "secure_score_percentage": (
                    float(row["secure_score_percentage"])
                    if row.get("secure_score_percentage") is not None
                    else None
                ),
            }
        )
    return summary
