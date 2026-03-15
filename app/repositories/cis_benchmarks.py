from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db


async def upsert_result(
    *,
    company_id: int,
    benchmark_category: str,
    check_id: str,
    check_name: str,
    status: str,
    details: str,
    run_at: datetime,
) -> None:
    await db.execute(
        """
        INSERT INTO cis_benchmark_results
            (company_id, benchmark_category, check_id, check_name, status, details, run_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            check_name = VALUES(check_name),
            status = VALUES(status),
            details = VALUES(details),
            run_at = VALUES(run_at)
        """,
        (company_id, benchmark_category, check_id, check_name, status, details, run_at),
    )


async def list_results(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT benchmark_category, check_id, check_name, status, details, run_at
        FROM cis_benchmark_results
        WHERE company_id = %s
        ORDER BY benchmark_category, check_id
        """,
        (company_id,),
    )
    results = []
    for row in rows:
        entry = dict(row)
        results.append(entry)
    return results


async def delete_results(company_id: int) -> None:
    await db.execute(
        "DELETE FROM cis_benchmark_results WHERE company_id = %s",
        (company_id,),
    )
