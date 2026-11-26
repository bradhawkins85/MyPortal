"""Customer satisfaction (CSAT) survey settings, surveys and reporting queries."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.core.database import db

_SURVEY_SELECT = (
    "SELECT s.id, s.ticket_id, s.company_id, s.technician_user_id, s.recipient_email, s.token_hash, "
    "s.status, s.rating, s.comment, s.error_message, s.follow_up_status, s.followed_up_by_user_id, "
    "s.followed_up_at, s.sent_at, s.expires_at, s.responded_at, s.created_at, "
    "t.subject AS ticket_subject, t.ticket_number AS ticket_number, c.name AS company_name, "
    "u.first_name AS technician_first_name, u.last_name AS technician_last_name, "
    "u.email AS technician_email "
    "FROM ticket_csat_surveys s "
    "LEFT JOIN tickets t ON t.id = s.ticket_id "
    "LEFT JOIN companies c ON c.id = s.company_id "
    "LEFT JOIN users u ON u.id = s.technician_user_id"
)

_INT_KEYS = ("id", "ticket_id", "company_id", "technician_user_id", "followed_up_by_user_id", "rating")


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    record = dict(row)
    for key in _INT_KEYS:
        if record.get(key) is not None:
            record[key] = int(record[key])
    return record


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


async def get_settings() -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT enabled, delay_minutes, low_score_threshold, expiry_days, enabled_at "
        "FROM csat_settings WHERE id = 1"
    )
    return dict(row) if row else None


async def save_settings(
    *,
    enabled: bool,
    delay_minutes: int,
    low_score_threshold: int,
    expiry_days: int,
    enabled_at: datetime | None,
) -> None:
    updated = await db.execute_rowcount(
        "UPDATE csat_settings SET enabled = %s, delay_minutes = %s, low_score_threshold = %s, "
        "expiry_days = %s, enabled_at = %s WHERE id = 1",
        (1 if enabled else 0, delay_minutes, low_score_threshold, expiry_days, enabled_at),
    )
    if not updated:
        await db.execute(
            "INSERT INTO csat_settings (id, enabled, delay_minutes, low_score_threshold, "
            "expiry_days, enabled_at) VALUES (1, %s, %s, %s, %s, %s)",
            (1 if enabled else 0, delay_minutes, low_score_threshold, expiry_days, enabled_at),
        )


async def list_excluded_company_ids() -> list[int]:
    rows = await db.fetch_all("SELECT company_id FROM csat_company_exclusions ORDER BY company_id")
    return [int(row["company_id"]) for row in rows or []]


async def replace_excluded_companies(company_ids: Iterable[int]) -> None:
    wanted = sorted({int(value) for value in company_ids if int(value) > 0})
    await db.execute("DELETE FROM csat_company_exclusions")
    if wanted:
        await db.execute_many(
            "INSERT INTO csat_company_exclusions (company_id) VALUES (%s)",
            [(company_id,) for company_id in wanted],
        )


# ---------------------------------------------------------------------------
# Surveys
# ---------------------------------------------------------------------------


async def list_due_tickets(
    *, resolved_before: datetime, resolved_after: datetime, limit: int
) -> list[dict[str, Any]]:
    """Resolved or closed tickets with no survey yet, inside the sending window."""

    rows = await db.fetch_all(
        "SELECT t.id, t.company_id, t.requester_id, t.requester_staff_id, t.assigned_user_id, "
        "t.subject, t.ticket_number, t.status "
        "FROM tickets t "
        "LEFT JOIN ticket_csat_surveys s ON s.ticket_id = t.id "
        "LEFT JOIN csat_company_exclusions x ON x.company_id = t.company_id "
        "WHERE s.id IS NULL AND x.company_id IS NULL "
        "AND t.status IN ('resolved', 'closed') AND t.merged_into_ticket_id IS NULL "
        "AND t.status_changed_at <= %s AND t.status_changed_at >= %s "
        "ORDER BY t.status_changed_at ASC, t.id ASC LIMIT %s",
        (resolved_before, resolved_after, limit),
    )
    return [dict(row) for row in rows or []]


async def claim_ticket(
    *,
    ticket_id: int,
    company_id: int | None,
    technician_user_id: int | None,
    recipient_email: str | None,
) -> int | None:
    """Create the survey row for a ticket; ``None`` when another worker already did."""

    try:
        survey_id = await db.execute_returning_lastrowid(
            "INSERT INTO ticket_csat_surveys (ticket_id, company_id, technician_user_id, recipient_email, status) "
            "VALUES (%s, %s, %s, %s, 'pending')",
            (ticket_id, company_id, technician_user_id, recipient_email),
        )
    except Exception:
        # The unique ticket_id constraint rejects a second survey for the ticket;
        # anything else is a real failure.
        if await db.fetch_one("SELECT id FROM ticket_csat_surveys WHERE ticket_id = %s", (ticket_id,)):
            return None
        raise
    return int(survey_id) if survey_id else None


async def mark_sent(survey_id: int, *, token_hash: str, sent_at: datetime, expires_at: datetime) -> None:
    await db.execute(
        "UPDATE ticket_csat_surveys SET status = 'sent', token_hash = %s, sent_at = %s, expires_at = %s, "
        "error_message = NULL WHERE id = %s",
        (token_hash, sent_at, expires_at, survey_id),
    )


async def mark_skipped(survey_id: int, reason: str) -> None:
    await db.execute(
        "UPDATE ticket_csat_surveys SET status = 'skipped', error_message = %s WHERE id = %s",
        (reason[:500], survey_id),
    )


async def mark_failed(survey_id: int, reason: str) -> None:
    await db.execute(
        "UPDATE ticket_csat_surveys SET status = 'failed', error_message = %s WHERE id = %s",
        (reason[:500], survey_id),
    )


async def get_by_token_hash(token_hash: str) -> dict[str, Any] | None:
    row = await db.fetch_one(_SURVEY_SELECT + " WHERE s.token_hash = %s", (token_hash,))
    return _normalise(row)


async def get_by_id(survey_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(_SURVEY_SELECT + " WHERE s.id = %s", (survey_id,))
    return _normalise(row)


async def get_by_ticket(ticket_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(_SURVEY_SELECT + " WHERE s.ticket_id = %s", (ticket_id,))
    return _normalise(row)


async def record_response(
    survey_id: int,
    *,
    rating: int,
    comment: str | None,
    responded_at: datetime,
    follow_up_status: str | None,
) -> bool:
    """Store the rating once; a second submission for the same survey is ignored."""

    updated = await db.execute_rowcount(
        "UPDATE ticket_csat_surveys SET status = 'responded', rating = %s, comment = %s, responded_at = %s, "
        "follow_up_status = %s WHERE id = %s AND status = 'sent'",
        (rating, comment, responded_at, follow_up_status, survey_id),
    )
    return bool(updated)


async def set_follow_up(survey_id: int, *, status: str, user_id: int | None, when: datetime | None) -> bool:
    updated = await db.execute_rowcount(
        "UPDATE ticket_csat_surveys SET follow_up_status = %s, followed_up_by_user_id = %s, followed_up_at = %s "
        "WHERE id = %s AND follow_up_status IS NOT NULL",
        (status, user_id, when, survey_id),
    )
    return bool(updated)


async def list_responses(
    *,
    since: datetime,
    company_id: int | None = None,
    follow_up_only: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = _SURVEY_SELECT + " WHERE s.status = 'responded' AND s.responded_at >= %s"
    params: list[Any] = [since]
    if company_id is not None:
        sql += " AND s.company_id = %s"
        params.append(company_id)
    if follow_up_only:
        sql += " AND s.follow_up_status = 'open'"
    sql += " ORDER BY s.responded_at DESC, s.id DESC LIMIT %s"
    params.append(limit)
    rows = await db.fetch_all(sql, tuple(params))
    return [record for record in (_normalise(row) for row in rows or []) if record]


async def summary_counts(*, since: datetime, company_id: int | None = None) -> dict[str, int]:
    sql = (
        "SELECT "
        "SUM(CASE WHEN status IN ('sent', 'responded') THEN 1 ELSE 0 END) AS sent, "
        "SUM(CASE WHEN status = 'responded' THEN 1 ELSE 0 END) AS responded, "
        "SUM(CASE WHEN status = 'responded' AND rating >= 4 THEN 1 ELSE 0 END) AS satisfied, "
        "SUM(CASE WHEN follow_up_status = 'open' THEN 1 ELSE 0 END) AS open_follow_ups, "
        "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed "
        "FROM ticket_csat_surveys WHERE COALESCE(sent_at, created_at) >= %s"
    )
    params: list[Any] = [since]
    if company_id is not None:
        sql += " AND company_id = %s"
        params.append(company_id)
    row = await db.fetch_one(sql, tuple(params)) or {}
    return {key: int(row.get(key) or 0) for key in ("sent", "responded", "satisfied", "open_follow_ups", "failed")}


async def breakdown_by_technician(*, since: datetime, company_id: int | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT s.technician_user_id, u.first_name, u.last_name, u.email, COUNT(*) AS responses, "
        "SUM(CASE WHEN s.rating >= 4 THEN 1 ELSE 0 END) AS satisfied, AVG(s.rating) AS average "
        "FROM ticket_csat_surveys s LEFT JOIN users u ON u.id = s.technician_user_id "
        "WHERE s.status = 'responded' AND s.responded_at >= %s"
    )
    params: list[Any] = [since]
    if company_id is not None:
        sql += " AND s.company_id = %s"
        params.append(company_id)
    sql += " GROUP BY s.technician_user_id, u.first_name, u.last_name, u.email ORDER BY responses DESC"
    rows = await db.fetch_all(sql, tuple(params))
    return [dict(row) for row in rows or []]


async def breakdown_by_company(*, since: datetime) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT s.company_id, c.name AS company_name, COUNT(*) AS responses, "
        "SUM(CASE WHEN s.rating >= 4 THEN 1 ELSE 0 END) AS satisfied, AVG(s.rating) AS average "
        "FROM ticket_csat_surveys s LEFT JOIN companies c ON c.id = s.company_id "
        "WHERE s.status = 'responded' AND s.responded_at >= %s "
        "GROUP BY s.company_id, c.name ORDER BY responses DESC",
        (since,),
    )
    return [dict(row) for row in rows or []]
