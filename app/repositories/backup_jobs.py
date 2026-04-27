"""Backup History repository.

CRUD helpers for ``backup_jobs`` (one row per company-defined job) and
``backup_job_events`` (one row per job per calendar day, recording the
most-recent reported status for that day).
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

from app.core.database import db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def generate_job_token() -> str:
    """Return a cryptographically random URL-safe token used by webhook URLs."""
    return secrets.token_urlsafe(32)


def _normalise_job(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    job = dict(row)
    if job.get("id") is not None:
        job["id"] = int(job["id"])
    if job.get("company_id") is not None:
        job["company_id"] = int(job["company_id"])
    job["is_active"] = bool(int(job.get("is_active") or 0))
    return job


def _normalise_event(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    event = dict(row)
    if event.get("id") is not None:
        event["id"] = int(event["id"])
    if event.get("backup_job_id") is not None:
        event["backup_job_id"] = int(event["backup_job_id"])
    return event


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------


async def list_jobs(
    *,
    company_id: int | None = None,
    include_inactive: bool = True,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if company_id is not None:
        clauses.append("company_id = %s")
        params.append(int(company_id))
    if not include_inactive:
        clauses.append("is_active = 1")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await db.fetch_all(
        f"""
        SELECT id, company_id, name, description, token, is_active,
               created_by, created_at, updated_at
        FROM backup_jobs
        {where}
        ORDER BY company_id, name
        """,
        tuple(params),
    )
    return [job for job in (_normalise_job(row) for row in rows) if job]


async def get_job(job_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT id, company_id, name, description, token, is_active,
               created_by, created_at, updated_at
        FROM backup_jobs WHERE id = %s
        """,
        (int(job_id),),
    )
    return _normalise_job(row)


async def get_job_by_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    row = await db.fetch_one(
        """
        SELECT id, company_id, name, description, token, is_active,
               created_by, created_at, updated_at
        FROM backup_jobs WHERE token = %s
        """,
        (token,),
    )
    return _normalise_job(row)


async def create_job(
    *,
    company_id: int,
    name: str,
    description: str | None,
    token: str,
    is_active: bool = True,
    created_by: int | None = None,
) -> dict[str, Any]:
    new_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO backup_jobs
            (company_id, name, description, token, is_active, created_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            int(company_id),
            name,
            description,
            token,
            1 if is_active else 0,
            int(created_by) if created_by else None,
        ),
    )
    job = await get_job(int(new_id))
    if job is None:  # pragma: no cover - defensive
        raise RuntimeError("Failed to load newly created backup job")
    return job


async def update_job(
    job_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    company_id: int | None = None,
    is_active: bool | None = None,
    token: str | None = None,
) -> dict[str, Any] | None:
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        sets.append("name = %s")
        params.append(name)
    if description is not None:
        sets.append("description = %s")
        params.append(description)
    if company_id is not None:
        sets.append("company_id = %s")
        params.append(int(company_id))
    if is_active is not None:
        sets.append("is_active = %s")
        params.append(1 if is_active else 0)
    if token is not None:
        sets.append("token = %s")
        params.append(token)
    if not sets:
        return await get_job(job_id)
    params.append(int(job_id))
    await db.execute(
        f"UPDATE backup_jobs SET {', '.join(sets)} WHERE id = %s",
        tuple(params),
    )
    return await get_job(job_id)


async def delete_job(job_id: int) -> None:
    await db.execute("DELETE FROM backup_jobs WHERE id = %s", (int(job_id),))


# ---------------------------------------------------------------------------
# Event CRUD
# ---------------------------------------------------------------------------


async def get_event(job_id: int, event_date: date) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT id, backup_job_id, event_date, status, status_message,
               reported_at, source, created_at, updated_at
        FROM backup_job_events
        WHERE backup_job_id = %s AND event_date = %s
        """,
        (int(job_id), event_date),
    )
    return _normalise_event(row)


async def upsert_event(
    job_id: int,
    event_date: date,
    *,
    status: str,
    status_message: str | None = None,
    reported_at: datetime | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Insert or update the event for ``(job_id, event_date)``.

    The ``UNIQUE`` constraint on ``(backup_job_id, event_date)`` keeps this
    idempotent: subsequent reports for the same calendar day overwrite the
    previous status (which is desirable — the latest report wins).
    """
    if db.is_sqlite():
        await db.execute(
            """
            INSERT INTO backup_job_events
                (backup_job_id, event_date, status, status_message,
                 reported_at, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(backup_job_id, event_date) DO UPDATE SET
                status = excluded.status,
                status_message = excluded.status_message,
                reported_at = excluded.reported_at,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(job_id),
                event_date,
                status,
                status_message,
                reported_at,
                source,
            ),
        )
    else:
        await db.execute(
            """
            INSERT INTO backup_job_events
                (backup_job_id, event_date, status, status_message,
                 reported_at, source)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                status_message = VALUES(status_message),
                reported_at = VALUES(reported_at),
                source = VALUES(source),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(job_id),
                event_date,
                status,
                status_message,
                reported_at,
                source,
            ),
        )
    event = await get_event(job_id, event_date)
    if event is None:  # pragma: no cover - defensive
        raise RuntimeError("Failed to load backup job event after upsert")
    return event


async def seed_unknown_event(job_id: int, event_date: date) -> bool:
    """Create an ``unknown`` event for the given day if none exists.

    Returns ``True`` when a new row was inserted, ``False`` when an event
    was already present (in which case the existing status is preserved).
    """
    existing = await get_event(job_id, event_date)
    if existing is not None:
        return False
    if db.is_sqlite():
        await db.execute(
            """
            INSERT OR IGNORE INTO backup_job_events
                (backup_job_id, event_date, status, source)
            VALUES (?, ?, 'unknown', 'scheduler')
            """,
            (int(job_id), event_date),
        )
    else:
        await db.execute(
            """
            INSERT IGNORE INTO backup_job_events
                (backup_job_id, event_date, status, source)
            VALUES (%s, %s, 'unknown', 'scheduler')
            """,
            (int(job_id), event_date),
        )
    return True


async def list_events_in_range(
    *,
    job_ids: Sequence[int] | None = None,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Return events between ``start_date`` and ``end_date`` inclusive."""
    clauses: list[str] = ["event_date >= %s", "event_date <= %s"]
    params: list[Any] = [start_date, end_date]
    if job_ids is not None:
        ids = [int(value) for value in job_ids]
        if not ids:
            return []
        placeholders = ", ".join(["%s"] * len(ids))
        clauses.append(f"backup_job_id IN ({placeholders})")
        params.extend(ids)
    where = "WHERE " + " AND ".join(clauses)
    rows = await db.fetch_all(
        f"""
        SELECT id, backup_job_id, event_date, status, status_message,
               reported_at, source, created_at, updated_at
        FROM backup_job_events
        {where}
        ORDER BY backup_job_id, event_date
        """,
        tuple(params),
    )
    return [event for event in (_normalise_event(row) for row in rows) if event]


async def latest_event_per_job(
    job_ids: Iterable[int],
) -> dict[int, dict[str, Any]]:
    """Return the most-recent event for each given job id."""
    ids = [int(value) for value in job_ids]
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    rows = await db.fetch_all(
        f"""
        SELECT e.id, e.backup_job_id, e.event_date, e.status, e.status_message,
               e.reported_at, e.source, e.created_at, e.updated_at
        FROM backup_job_events e
        INNER JOIN (
            SELECT backup_job_id, MAX(event_date) AS max_date
            FROM backup_job_events
            WHERE backup_job_id IN ({placeholders})
            GROUP BY backup_job_id
        ) latest
          ON e.backup_job_id = latest.backup_job_id
         AND e.event_date = latest.max_date
        """,
        tuple(ids),
    )
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        normalised = _normalise_event(row)
        if normalised is None:
            continue
        out[int(normalised["backup_job_id"])] = normalised
    return out


async def list_active_job_ids() -> list[int]:
    rows = await db.fetch_all(
        "SELECT id FROM backup_jobs WHERE is_active = 1",
    )
    return [int(row["id"]) for row in rows if row.get("id") is not None]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "create_job",
    "delete_job",
    "generate_job_token",
    "get_event",
    "get_job",
    "get_job_by_token",
    "latest_event_per_job",
    "list_active_job_ids",
    "list_events_in_range",
    "list_jobs",
    "seed_unknown_event",
    "update_job",
    "upsert_event",
    "utc_now",
]
