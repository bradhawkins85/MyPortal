"""Service agreements (:mod:`app.services.service_agreements`)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

from app.core.database import db

_SELECT = (
    "SELECT a.id, a.company_id, a.name, a.agreement_type, a.hours_allowance, a.period, a.start_date, a.end_date, "
    "a.fee_amount, a.alert_percent, a.notes, a.is_cancelled, a.created_by_user_id, a.created_at, a.updated_at, "
    "c.name AS company_name FROM service_agreements a LEFT JOIN companies c ON c.id = a.company_id"
)
_COLUMNS = ("company_id", "name", "agreement_type", "hours_allowance", "period", "start_date", "end_date",
            "fee_amount", "alert_percent", "notes", "is_cancelled")


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    record = dict(row)
    for key in ("id", "company_id", "alert_percent", "created_by_user_id"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    for key in ("hours_allowance", "fee_amount"):
        if record.get(key) is not None:
            record[key] = float(record[key])
    record["start_date"] = _as_date(record.get("start_date"))
    record["end_date"] = _as_date(record.get("end_date"))
    record["is_cancelled"] = bool(record.get("is_cancelled"))
    return record


async def _attach_labour_types(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not records:
        return records
    ids = [record["id"] for record in records]
    placeholders = ", ".join(["%s"] * len(ids))
    sql = "SELECT agreement_id, labour_type_id FROM service_agreement_labour_types WHERE agreement_id IN (" + placeholders + ")"  # nosec B608 - placeholders only
    rows = await db.fetch_all(sql, tuple(ids))
    by_agreement: dict[int, list[int]] = {}
    for row in rows or []:
        by_agreement.setdefault(int(row["agreement_id"]), []).append(int(row["labour_type_id"]))
    for record in records:
        record["labour_type_ids"] = sorted(by_agreement.get(record["id"], []))
    return records


async def list_agreements(*, company_id: int | None = None, include_cancelled: bool = True) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if company_id is not None:
        conditions.append("a.company_id = %s")
        params.append(company_id)
    if not include_cancelled:
        conditions.append("a.is_cancelled = 0")
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = await db.fetch_all(_SELECT + where + " ORDER BY c.name, a.start_date DESC, a.id DESC", tuple(params))  # nosec B608 - fixed conditions, bound values
    return await _attach_labour_types([record for record in (_normalise(row) for row in rows or []) if record])


async def get_agreement(agreement_id: int) -> dict[str, Any] | None:
    record = _normalise(await db.fetch_one(_SELECT + " WHERE a.id = %s", (agreement_id,)))
    if record is None:
        return None
    return (await _attach_labour_types([record]))[0]


async def _replace_labour_types(agreement_id: int, labour_type_ids: Iterable[int]) -> None:
    await db.execute("DELETE FROM service_agreement_labour_types WHERE agreement_id = %s", (agreement_id,))
    wanted = sorted({int(value) for value in labour_type_ids})
    if wanted:
        await db.execute_many(
            "INSERT INTO service_agreement_labour_types (agreement_id, labour_type_id) VALUES (%s, %s)",
            [(agreement_id, labour_type_id) for labour_type_id in wanted],
        )


async def create_agreement(values: dict[str, Any], *, user_id: int | None) -> int:
    params = tuple(values.get(column) for column in _COLUMNS) + (user_id,)
    agreement_id = int(await db.execute_returning_lastrowid(
        "INSERT INTO service_agreements (company_id, name, agreement_type, hours_allowance, period, start_date, "
        "end_date, fee_amount, alert_percent, notes, is_cancelled, created_by_user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        params,
    ))
    await _replace_labour_types(agreement_id, values.get("labour_type_ids") or [])
    return agreement_id


async def update_agreement(agreement_id: int, values: dict[str, Any]) -> None:
    params = tuple(values.get(column) for column in _COLUMNS) + (agreement_id,)
    await db.execute(
        "UPDATE service_agreements SET company_id = %s, name = %s, agreement_type = %s, hours_allowance = %s, "
        "period = %s, start_date = %s, end_date = %s, fee_amount = %s, alert_percent = %s, notes = %s, "
        "is_cancelled = %s WHERE id = %s",
        params,
    )
    await _replace_labour_types(agreement_id, values.get("labour_type_ids") or [])


async def delete_agreement(agreement_id: int) -> None:
    await db.execute("DELETE FROM service_agreements WHERE id = %s", (agreement_id,))


async def list_time_entries(company_ids: Sequence[int], start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Ticket time entries for the companies between ``start`` and ``end`` (UTC)."""

    if not company_ids:
        return []
    placeholders = ", ".join(["%s"] * len(company_ids))
    company_filter = "t.company_id IN (" + placeholders + ")"  # nosec B608 - placeholders only
    rows = await db.fetch_all(
        "SELECT tr.id, tr.ticket_id, tr.minutes_spent, tr.labour_type_id, tr.is_billable, tr.created_at, "
        "t.company_id, t.ticket_number, t.subject, lt.name AS labour_type_name, lt.rate AS labour_rate "
        "FROM ticket_replies tr JOIN tickets t ON t.id = tr.ticket_id "
        "LEFT JOIN ticket_labour_types lt ON lt.id = tr.labour_type_id "
        "WHERE tr.minutes_spent IS NOT NULL AND tr.minutes_spent > 0 AND tr.created_at >= %s AND tr.created_at < %s "
        "AND " + company_filter + " ORDER BY tr.created_at ASC, tr.id ASC",  # nosec B608 - fixed filter, bound values
        (start, end) + tuple(company_ids),
    )
    return [dict(row) for row in rows or []]


async def record_alert(agreement_id: int, kind: str, period_start: date) -> bool:
    """Remember an alert; ``False`` when it was already raised for this period."""

    existing = await db.fetch_one(
        "SELECT id FROM service_agreement_alerts WHERE agreement_id = %s AND kind = %s AND period_start = %s",
        (agreement_id, kind, period_start),
    )
    if existing:
        return False
    try:
        await db.execute(
            "INSERT INTO service_agreement_alerts (agreement_id, kind, period_start) VALUES (%s, %s, %s)",
            (agreement_id, kind, period_start),
        )
    except Exception:
        # Another instance recorded it first (unique key).
        return False
    return True
