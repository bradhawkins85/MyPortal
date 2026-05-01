"""Repository helpers for Huntress snapshot tables.

All values displayed by the Huntress report sections come from these tables —
the daily Huntress sync writes here, and report rendering reads from here. No
live API calls happen during report generation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from app.core.database import db


# ---------------------------------------------------------------------------
# EDR
# ---------------------------------------------------------------------------


async def get_edr_stats(company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM huntress_edr_stats WHERE company_id = %s",
        (company_id,),
    )
    return dict(row) if row else None


async def upsert_edr_stats(
    company_id: int,
    *,
    active_incidents: int,
    resolved_incidents: int,
    signals_investigated: int,
    snapshot_at: datetime | None = None,
) -> None:
    snapshot = snapshot_at or datetime.utcnow()
    existing = await db.fetch_one(
        "SELECT company_id FROM huntress_edr_stats WHERE company_id = %s",
        (company_id,),
    )
    if existing:
        await db.execute(
            """
            UPDATE huntress_edr_stats
               SET active_incidents = %s,
                   resolved_incidents = %s,
                   signals_investigated = %s,
                   snapshot_at = %s
             WHERE company_id = %s
            """,
            (
                int(active_incidents),
                int(resolved_incidents),
                int(signals_investigated),
                snapshot,
                int(company_id),
            ),
        )
    else:
        await db.execute(
            """
            INSERT INTO huntress_edr_stats
                (company_id, active_incidents, resolved_incidents,
                 signals_investigated, snapshot_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                int(company_id),
                int(active_incidents),
                int(resolved_incidents),
                int(signals_investigated),
                snapshot,
            ),
        )


# ---------------------------------------------------------------------------
# ITDR
# ---------------------------------------------------------------------------


async def get_itdr_stats(company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM huntress_itdr_stats WHERE company_id = %s",
        (company_id,),
    )
    return dict(row) if row else None


async def upsert_itdr_stats(
    company_id: int,
    *,
    signals_investigated: int,
    snapshot_at: datetime | None = None,
) -> None:
    snapshot = snapshot_at or datetime.utcnow()
    existing = await db.fetch_one(
        "SELECT company_id FROM huntress_itdr_stats WHERE company_id = %s",
        (company_id,),
    )
    if existing:
        await db.execute(
            """
            UPDATE huntress_itdr_stats
               SET signals_investigated = %s, snapshot_at = %s
             WHERE company_id = %s
            """,
            (int(signals_investigated), snapshot, int(company_id)),
        )
    else:
        await db.execute(
            """
            INSERT INTO huntress_itdr_stats
                (company_id, signals_investigated, snapshot_at)
            VALUES (%s, %s, %s)
            """,
            (int(company_id), int(signals_investigated), snapshot),
        )


# ---------------------------------------------------------------------------
# SIEM
# ---------------------------------------------------------------------------


async def get_siem_stats(company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM huntress_siem_stats WHERE company_id = %s",
        (company_id,),
    )
    return dict(row) if row else None


async def upsert_siem_stats(
    company_id: int,
    *,
    data_collected_bytes_30d: int,
    window_start: datetime | None,
    window_end: datetime | None,
    snapshot_at: datetime | None = None,
) -> None:
    snapshot = snapshot_at or datetime.utcnow()
    existing = await db.fetch_one(
        "SELECT company_id FROM huntress_siem_stats WHERE company_id = %s",
        (company_id,),
    )
    if existing:
        await db.execute(
            """
            UPDATE huntress_siem_stats
               SET data_collected_bytes_30d = %s,
                   window_start = %s,
                   window_end = %s,
                   snapshot_at = %s
             WHERE company_id = %s
            """,
            (
                int(data_collected_bytes_30d),
                window_start,
                window_end,
                snapshot,
                int(company_id),
            ),
        )
    else:
        await db.execute(
            """
            INSERT INTO huntress_siem_stats
                (company_id, data_collected_bytes_30d, window_start, window_end, snapshot_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                int(company_id),
                int(data_collected_bytes_30d),
                window_start,
                window_end,
                snapshot,
            ),
        )


# ---------------------------------------------------------------------------
# SOC
# ---------------------------------------------------------------------------


async def get_soc_stats(company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM huntress_soc_stats WHERE company_id = %s",
        (company_id,),
    )
    return dict(row) if row else None


async def upsert_soc_stats(
    company_id: int,
    *,
    total_events_analysed: int,
    snapshot_at: datetime | None = None,
) -> None:
    snapshot = snapshot_at or datetime.utcnow()
    existing = await db.fetch_one(
        "SELECT company_id FROM huntress_soc_stats WHERE company_id = %s",
        (company_id,),
    )
    if existing:
        await db.execute(
            """
            UPDATE huntress_soc_stats
               SET total_events_analysed = %s, snapshot_at = %s
             WHERE company_id = %s
            """,
            (int(total_events_analysed), snapshot, int(company_id)),
        )
    else:
        await db.execute(
            """
            INSERT INTO huntress_soc_stats
                (company_id, total_events_analysed, snapshot_at)
            VALUES (%s, %s, %s)
            """,
            (int(company_id), int(total_events_analysed), snapshot),
        )


# ---------------------------------------------------------------------------
# SAT (summary + per-learner)
# ---------------------------------------------------------------------------


async def get_sat_stats(company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM huntress_sat_stats WHERE company_id = %s",
        (company_id,),
    )
    return dict(row) if row else None


async def upsert_sat_stats(
    company_id: int,
    *,
    avg_completion_rate: float,
    avg_score: float,
    phishing_clicks: int,
    phishing_compromises: int,
    phishing_reports: int,
    snapshot_at: datetime | None = None,
) -> None:
    snapshot = snapshot_at or datetime.utcnow()
    existing = await db.fetch_one(
        "SELECT company_id FROM huntress_sat_stats WHERE company_id = %s",
        (company_id,),
    )
    if existing:
        await db.execute(
            """
            UPDATE huntress_sat_stats
               SET avg_completion_rate = %s,
                   avg_score = %s,
                   phishing_clicks = %s,
                   phishing_compromises = %s,
                   phishing_reports = %s,
                   snapshot_at = %s
             WHERE company_id = %s
            """,
            (
                float(avg_completion_rate),
                float(avg_score),
                int(phishing_clicks),
                int(phishing_compromises),
                int(phishing_reports),
                snapshot,
                int(company_id),
            ),
        )
    else:
        await db.execute(
            """
            INSERT INTO huntress_sat_stats
                (company_id, avg_completion_rate, avg_score,
                 phishing_clicks, phishing_compromises, phishing_reports, snapshot_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(company_id),
                float(avg_completion_rate),
                float(avg_score),
                int(phishing_clicks),
                int(phishing_compromises),
                int(phishing_reports),
                snapshot,
            ),
        )


async def list_sat_learner_progress(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT *
          FROM huntress_sat_learner_assignments
         WHERE company_id = %s
         ORDER BY learner_name, learner_email, assignment_name
        """,
        (company_id,),
    )
    return [dict(row) for row in rows]


async def replace_sat_learner_progress(
    company_id: int,
    rows: Iterable[Mapping[str, Any]],
    *,
    snapshot_at: datetime | None = None,
) -> int:
    """Replace all learner/assignment rows for a company in one transaction."""

    snapshot = snapshot_at or datetime.utcnow()
    materialised = list(rows)
    await db.execute(
        "DELETE FROM huntress_sat_learner_assignments WHERE company_id = %s",
        (int(company_id),),
    )
    for row in materialised:
        learner_external_id = str(row.get("learner_external_id") or "").strip()
        assignment_id = str(row.get("assignment_id") or "").strip()
        if not learner_external_id or not assignment_id:
            continue
        await db.execute(
            """
            INSERT INTO huntress_sat_learner_assignments
                (company_id, learner_external_id, learner_email, learner_name,
                 assignment_id, assignment_name, status, completion_percent,
                 score, click_rate, compromise_rate, report_rate, snapshot_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(company_id),
                learner_external_id,
                row.get("learner_email"),
                row.get("learner_name"),
                assignment_id,
                row.get("assignment_name"),
                row.get("status"),
                float(row.get("completion_percent") or 0),
                float(row.get("score") or 0),
                float(row.get("click_rate") or 0),
                float(row.get("compromise_rate") or 0),
                float(row.get("report_rate") or 0),
                snapshot,
            ),
        )
    return len(materialised)


__all__ = [
    "get_edr_stats",
    "upsert_edr_stats",
    "get_itdr_stats",
    "upsert_itdr_stats",
    "get_siem_stats",
    "upsert_siem_stats",
    "get_soc_stats",
    "upsert_soc_stats",
    "get_sat_stats",
    "upsert_sat_stats",
    "list_sat_learner_progress",
    "replace_sat_learner_progress",
]
