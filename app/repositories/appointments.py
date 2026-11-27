"""Technician appointments and scheduling settings (:mod:`app.services.scheduling`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db

_SELECT = (
    "SELECT a.id, a.ticket_id, a.company_id, a.technician_user_id, a.kind, a.starts_at, a.ends_at, a.location, "
    "a.notes, a.status, a.m365_event_id, a.m365_mailbox, a.sync_status, a.sync_error, a.sync_attempts, "
    "a.created_by_user_id, a.created_at, a.updated_at, "
    "t.ticket_number, t.subject AS ticket_subject, t.status AS ticket_status, c.name AS company_name, "
    "u.first_name AS technician_first_name, u.last_name AS technician_last_name, u.email AS technician_email "
    "FROM appointments a "
    "LEFT JOIN tickets t ON t.id = a.ticket_id "
    "LEFT JOIN companies c ON c.id = a.company_id "
    "LEFT JOIN users u ON u.id = a.technician_user_id"
)
_COLUMNS = ("ticket_id", "company_id", "technician_user_id", "kind", "starts_at", "ends_at", "location", "notes",
            "status")


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    record = dict(row)
    for key in ("id", "ticket_id", "company_id", "technician_user_id", "created_by_user_id", "sync_attempts"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    return record


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [record for record in (_normalise(row) for row in rows or []) if record]


async def get(appointment_id: int) -> dict[str, Any] | None:
    return _normalise(await db.fetch_one(_SELECT + " WHERE a.id = %s", (appointment_id,)))


async def list_between(start: datetime, end: datetime, *, technician_user_id: int | None = None,
                       include_cancelled: bool = False) -> list[dict[str, Any]]:
    """Appointments overlapping ``start``–``end``."""

    sql = _SELECT + " WHERE a.starts_at < %s AND a.ends_at > %s"
    params: list[Any] = [end, start]
    if technician_user_id is not None:
        sql += " AND a.technician_user_id = %s"
        params.append(technician_user_id)
    if not include_cancelled:
        sql += " AND a.status <> 'cancelled'"
    return _rows(await db.fetch_all(sql + " ORDER BY a.starts_at, a.id", tuple(params)))


async def list_for_ticket(ticket_id: int) -> list[dict[str, Any]]:
    return _rows(await db.fetch_all(_SELECT + " WHERE a.ticket_id = %s ORDER BY a.starts_at DESC, a.id DESC",
                                    (ticket_id,)))


async def list_overlapping(technician_user_id: int, start: datetime, end: datetime,
                           exclude_id: int | None) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        _SELECT + " WHERE a.technician_user_id = %s AND a.status = 'scheduled' AND a.starts_at < %s "
        "AND a.ends_at > %s AND a.id <> %s ORDER BY a.starts_at",
        (technician_user_id, end, start, exclude_id or 0),
    )
    return _rows(rows)


async def list_sync_due(limit: int = 50) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        _SELECT + " WHERE a.sync_status IN ('pending', 'failed') AND a.sync_attempts < 5 ORDER BY a.updated_at LIMIT %s",
        (limit,),
    )
    return _rows(rows)


async def create(values: dict[str, Any], *, user_id: int | None, sync_status: str) -> int:
    params = tuple(values.get(column) for column in _COLUMNS) + (sync_status, user_id)
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO appointments (ticket_id, company_id, technician_user_id, kind, starts_at, ends_at, location, "
        "notes, status, sync_status, created_by_user_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        params,
    ))


async def update(appointment_id: int, values: dict[str, Any], *, sync_status: str) -> None:
    params = tuple(values.get(column) for column in _COLUMNS) + (sync_status, appointment_id)
    await db.execute(
        "UPDATE appointments SET ticket_id = %s, company_id = %s, technician_user_id = %s, kind = %s, starts_at = %s, "
        "ends_at = %s, location = %s, notes = %s, status = %s, sync_status = %s, sync_attempts = 0, sync_error = NULL "
        "WHERE id = %s",
        params,
    )


async def set_status(appointment_id: int, status: str, *, sync_status: str) -> None:
    await db.execute(
        "UPDATE appointments SET status = %s, sync_status = %s, sync_attempts = 0, sync_error = NULL WHERE id = %s",
        (status, sync_status, appointment_id),
    )


async def record_sync(appointment_id: int, *, sync_status: str, event_id: str | None, mailbox: str | None,
                      error: str | None) -> None:
    await db.execute(
        "UPDATE appointments SET sync_status = %s, m365_event_id = %s, m365_mailbox = %s, sync_error = %s, "
        "sync_attempts = sync_attempts + 1 WHERE id = %s",
        (sync_status, event_id, mailbox, (error or "")[:500] or None, appointment_id),
    )


async def delete(appointment_id: int) -> None:
    await db.execute("DELETE FROM appointments WHERE id = %s", (appointment_id,))


async def get_settings() -> dict[str, Any]:
    row = await db.fetch_one(
        "SELECT m365_company_id, timezone, day_start_hour, day_end_hour, default_duration_minutes "
        "FROM scheduling_settings WHERE id = 1"
    )
    return dict(row) if row else {}


async def get_busy_sync_state() -> dict[str, Any]:
    row = await db.fetch_one("SELECT busy_synced_at, busy_sync_error FROM scheduling_settings WHERE id = 1")
    return dict(row) if row else {}


async def record_busy_sync(*, synced_at: datetime, error: str | None) -> None:
    await db.execute(
        "UPDATE scheduling_settings SET busy_synced_at = %s, busy_sync_error = %s WHERE id = 1",
        (synced_at, (error or "")[:500] or None),
    )


async def save_settings(values: dict[str, Any]) -> None:
    params = (values.get("m365_company_id"), values.get("timezone"), values["day_start_hour"],
              values["day_end_hour"], values["default_duration_minutes"])
    updated = await db.execute_rowcount(
        "UPDATE scheduling_settings SET m365_company_id = %s, timezone = %s, day_start_hour = %s, day_end_hour = %s, "
        "default_duration_minutes = %s WHERE id = 1",
        params,
    )
    if not updated:
        await db.execute(
            "INSERT INTO scheduling_settings (id, m365_company_id, timezone, day_start_hour, day_end_hour, "
            "default_duration_minutes) VALUES (1, %s, %s, %s, %s, %s)",
            params,
        )


async def list_unscheduled_tickets(now: datetime, limit: int = 100) -> list[dict[str, Any]]:
    """Open tickets with no upcoming appointment, most recently updated first."""

    rows = await db.fetch_all(
        "SELECT t.id, t.ticket_number, t.subject, t.priority, t.status, t.company_id, t.assigned_user_id, "
        "c.name AS company_name FROM tickets t LEFT JOIN companies c ON c.id = t.company_id "
        "WHERE t.status NOT IN ('resolved', 'closed') AND t.merged_into_ticket_id IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.ticket_id = t.id AND a.status = 'scheduled' "
        "AND a.ends_at > %s) ORDER BY t.updated_at DESC, t.id DESC LIMIT %s",
        (now, limit),
    )
    return [dict(row) for row in rows or []]


# --------------------------------------------------------------------------- #
# Busy time imported from technicians' calendars (no event details are stored)
# --------------------------------------------------------------------------- #


async def replace_busy_times(technician_user_id: int, start: datetime, end: datetime,
                             blocks: list[tuple[datetime, datetime, str]], *, synced_at: datetime) -> None:
    """Replace a technician's imported busy time overlapping ``start``–``end``."""

    await db.execute(
        "DELETE FROM technician_busy_times WHERE technician_user_id = %s AND starts_at < %s AND ends_at > %s",
        (technician_user_id, end, start),
    )
    if blocks:
        await db.execute_many(
            "INSERT INTO technician_busy_times (technician_user_id, starts_at, ends_at, show_as, synced_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(technician_user_id, block_start, block_end, show_as, synced_at)
             for block_start, block_end, show_as in blocks],
        )


async def clear_busy_times() -> None:
    await db.execute("DELETE FROM technician_busy_times")


async def list_busy_between(start: datetime, end: datetime, *,
                            technician_user_id: int | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT id, technician_user_id, starts_at, ends_at, show_as FROM technician_busy_times "
           "WHERE starts_at < %s AND ends_at > %s")
    params: list[Any] = [end, start]
    if technician_user_id is not None:
        sql += " AND technician_user_id = %s"
        params.append(technician_user_id)
    rows = await db.fetch_all(sql + " ORDER BY starts_at, id", tuple(params))
    return [{**dict(row), "id": int(row["id"]), "technician_user_id": int(row["technician_user_id"])}
            for row in rows or []]
