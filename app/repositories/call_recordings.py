from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _coerce_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value).replace(tzinfo=None)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return _ensure_utc(parsed).replace(tzinfo=None)


def _map_recording_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map database row to recording dict with proper type conversions."""
    mapped = dict(row)
    
    # Convert datetime fields
    if "call_date" in mapped and mapped["call_date"]:
        mapped["call_date"] = _coerce_datetime(mapped["call_date"])
    if "created_at" in mapped and mapped["created_at"]:
        mapped["created_at"] = _coerce_datetime(mapped["created_at"])
    if "updated_at" in mapped and mapped["updated_at"]:
        mapped["updated_at"] = _coerce_datetime(mapped["updated_at"])
    
    # Convert int fields
    for field in ["id", "caller_staff_id", "callee_staff_id", "duration_seconds", "linked_ticket_id", "minutes_spent", "labour_type_id"]:
        if field in mapped and mapped[field] is not None:
            mapped[field] = int(mapped[field])
    
    # Convert boolean fields
    if "is_billable" in mapped:
        mapped["is_billable"] = bool(mapped["is_billable"])
    
    return mapped


async def list_call_recordings(
    *,
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    transcription_status: str | None = None,
    linked_ticket_id: int | None = None,
) -> list[dict[str, Any]]:
    """List call recordings with optional filtering."""
    conditions: list[str] = []
    params: list[Any] = []
    
    if search:
        conditions.append(
            "(cr.file_name LIKE %s OR cr.caller_number LIKE %s OR cr.callee_number LIKE %s "
            "OR cs.first_name LIKE %s OR cs.last_name LIKE %s "
            "OR ce.first_name LIKE %s OR ce.last_name LIKE %s)"
        )
        search_pattern = f"%{search}%"
        params.extend([search_pattern] * 7)
    
    if transcription_status:
        conditions.append("cr.transcription_status = %s")
        params.append(transcription_status)
    
    if linked_ticket_id is not None:
        if linked_ticket_id == 0:
            conditions.append("cr.linked_ticket_id IS NULL")
        else:
            conditions.append("cr.linked_ticket_id = %s")
            params.append(linked_ticket_id)
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
        SELECT 
            cr.*,
            cs.first_name AS caller_first_name,
            cs.last_name AS caller_last_name,
            cs.email AS caller_email,
            ce.first_name AS callee_first_name,
            ce.last_name AS callee_last_name,
            ce.email AS callee_email,
            t.ticket_number AS linked_ticket_number,
            t.subject AS linked_ticket_subject,
            lt.name AS labour_type_name,
            lt.code AS labour_type_code
        FROM call_recordings cr
        LEFT JOIN staff cs ON cr.caller_staff_id = cs.id
        LEFT JOIN staff ce ON cr.callee_staff_id = ce.id
        LEFT JOIN tickets t ON cr.linked_ticket_id = t.id
        LEFT JOIN ticket_labour_types lt ON cr.labour_type_id = lt.id
        WHERE {where_clause}
        ORDER BY cr.call_date DESC
        LIMIT %s OFFSET %s
    """
    
    params.extend([limit, offset])
    rows = await db.fetch_all(sql, tuple(params))
    return [_map_recording_row(row) for row in rows]


async def count_call_recordings(
    *,
    search: str | None = None,
    transcription_status: str | None = None,
    linked_ticket_id: int | None = None,
) -> int:
    """Count call recordings with optional filtering."""
    conditions: list[str] = []
    params: list[Any] = []
    
    if search:
        conditions.append(
            "(cr.file_name LIKE %s OR cr.caller_number LIKE %s OR cr.callee_number LIKE %s "
            "OR cs.first_name LIKE %s OR cs.last_name LIKE %s "
            "OR ce.first_name LIKE %s OR ce.last_name LIKE %s)"
        )
        search_pattern = f"%{search}%"
        params.extend([search_pattern] * 7)
    
    if transcription_status:
        conditions.append("cr.transcription_status = %s")
        params.append(transcription_status)
    
    if linked_ticket_id is not None:
        if linked_ticket_id == 0:
            conditions.append("cr.linked_ticket_id IS NULL")
        else:
            conditions.append("cr.linked_ticket_id = %s")
            params.append(linked_ticket_id)
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
        SELECT COUNT(*) AS count
        FROM call_recordings cr
        LEFT JOIN staff cs ON cr.caller_staff_id = cs.id
        LEFT JOIN staff ce ON cr.callee_staff_id = ce.id
        WHERE {where_clause}
    """
    
    row = await db.fetch_one(sql, tuple(params))
    return int(row["count"]) if row else 0


async def get_call_recording_by_id(recording_id: int) -> dict[str, Any] | None:
    """Get a single call recording by ID."""
    sql = """
        SELECT
            cr.*,
            cs.first_name AS caller_first_name,
            cs.last_name AS caller_last_name,
            cs.email AS caller_email,
            ce.first_name AS callee_first_name,
            ce.last_name AS callee_last_name,
            ce.email AS callee_email,
            t.ticket_number AS linked_ticket_number,
            t.subject AS linked_ticket_subject,
            lt.name AS labour_type_name,
            lt.code AS labour_type_code
        FROM call_recordings cr
        LEFT JOIN staff cs ON cr.caller_staff_id = cs.id
        LEFT JOIN staff ce ON cr.callee_staff_id = ce.id
        LEFT JOIN tickets t ON cr.linked_ticket_id = t.id
        LEFT JOIN ticket_labour_types lt ON cr.labour_type_id = lt.id
        WHERE cr.id = %s
    """
    row = await db.fetch_one(sql, (recording_id,))
    return _map_recording_row(row) if row else None


async def get_call_recording_by_file_path(file_path: str) -> dict[str, Any] | None:
    """Return the call recording matching a file path, if it exists."""
    sql = """
        SELECT
            cr.*,
            cs.first_name AS caller_first_name,
            cs.last_name AS caller_last_name,
            cs.email AS caller_email,
            ce.first_name AS callee_first_name,
            ce.last_name AS callee_last_name,
            ce.email AS callee_email,
            t.ticket_number AS linked_ticket_number,
            t.subject AS linked_ticket_subject,
            lt.name AS labour_type_name,
            lt.code AS labour_type_code
        FROM call_recordings cr
        LEFT JOIN staff cs ON cr.caller_staff_id = cs.id
        LEFT JOIN staff ce ON cr.callee_staff_id = ce.id
        LEFT JOIN tickets t ON cr.linked_ticket_id = t.id
        LEFT JOIN ticket_labour_types lt ON cr.labour_type_id = lt.id
        WHERE cr.file_path = %s
        LIMIT 1
    """
    row = await db.fetch_one(sql, (file_path,))
    return _map_recording_row(row) if row else None


async def create_call_recording(
    *,
    file_path: str,
    file_name: str,
    caller_number: str | None = None,
    callee_number: str | None = None,
    call_date: datetime,
    duration_seconds: int | None = None,
    transcription: str | None = None,
    transcription_status: str = "pending",
) -> dict[str, Any]:
    """Create a new call recording."""
    # Lookup staff by phone numbers
    caller_staff_id = None
    callee_staff_id = None
    
    if caller_number:
        caller_staff_id = await _lookup_staff_by_phone(caller_number)
    if callee_number:
        callee_staff_id = await _lookup_staff_by_phone(callee_number)
    
    await db.execute(
        """
        INSERT INTO call_recordings (
            file_path, file_name, caller_number, callee_number,
            caller_staff_id, callee_staff_id, call_date, duration_seconds,
            transcription, transcription_status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            file_path,
            file_name,
            caller_number,
            callee_number,
            caller_staff_id,
            callee_staff_id,
            _coerce_datetime(call_date),
            duration_seconds,
            transcription,
            transcription_status,
        ),
    )
    
    created = await db.fetch_one("SELECT * FROM call_recordings WHERE id = LAST_INSERT_ID()")
    if not created:
        raise RuntimeError("Failed to create call recording")
    return _map_recording_row(created)


async def update_call_recording(
    recording_id: int,
    *,
    transcription: str | None = None,
    transcription_status: str | None = None,
    linked_ticket_id: int | None = None,
    minutes_spent: int | None = None,
    is_billable: bool | None = None,
    labour_type_id: int | None = None,
) -> dict[str, Any]:
    """Update an existing call recording."""
    updates: list[str] = []
    params: list[Any] = []
    
    if transcription is not None:
        updates.append("transcription = %s")
        params.append(transcription)
    
    if transcription_status is not None:
        updates.append("transcription_status = %s")
        params.append(transcription_status)
    
    if linked_ticket_id is not None:
        updates.append("linked_ticket_id = %s")
        params.append(linked_ticket_id)
    
    if minutes_spent is not None:
        updates.append("minutes_spent = %s")
        params.append(minutes_spent)
    
    if is_billable is not None:
        updates.append("is_billable = %s")
        params.append(1 if is_billable else 0)
    
    if labour_type_id is not None:
        updates.append("labour_type_id = %s")
        params.append(labour_type_id)
    
    if not updates:
        # No updates to perform
        return await get_call_recording_by_id(recording_id)
    
    params.append(recording_id)
    sql = f"UPDATE call_recordings SET {', '.join(updates)} WHERE id = %s"
    await db.execute(sql, tuple(params))
    
    updated = await get_call_recording_by_id(recording_id)
    if not updated:
        raise ValueError("Call recording not found after update")
    return updated


async def delete_call_recording(recording_id: int) -> None:
    """Delete a call recording."""
    await db.execute("DELETE FROM call_recordings WHERE id = %s", (recording_id,))


async def link_recording_to_ticket(recording_id: int, ticket_id: int) -> dict[str, Any]:
    """Link a call recording to a ticket."""
    return await update_call_recording(recording_id, linked_ticket_id=ticket_id)


async def unlink_recording_from_ticket(recording_id: int) -> dict[str, Any]:
    """Unlink a call recording from its ticket."""
    await db.execute(
        "UPDATE call_recordings SET linked_ticket_id = NULL WHERE id = %s",
        (recording_id,)
    )
    updated = await get_call_recording_by_id(recording_id)
    if not updated:
        raise ValueError("Call recording not found after unlink")
    return updated


async def list_ticket_call_recordings(ticket_id: int) -> list[dict[str, Any]]:
    """List all call recordings linked to a specific ticket."""
    sql = """
        SELECT
            cr.*,
            cs.first_name AS caller_first_name,
            cs.last_name AS caller_last_name,
            cs.email AS caller_email,
            ce.first_name AS callee_first_name,
            ce.last_name AS callee_last_name,
            ce.email AS callee_email,
            lt.name AS labour_type_name,
            lt.code AS labour_type_code
        FROM call_recordings cr
        LEFT JOIN staff cs ON cr.caller_staff_id = cs.id
        LEFT JOIN staff ce ON cr.callee_staff_id = ce.id
        LEFT JOIN ticket_labour_types lt ON cr.labour_type_id = lt.id
        WHERE cr.linked_ticket_id = %s
        ORDER BY cr.call_date ASC
    """
    rows = await db.fetch_all(sql, (ticket_id,))
    return [_map_recording_row(row) for row in rows]


async def _lookup_staff_by_phone(phone_number: str) -> int | None:
    """Look up staff member by phone number (mobile or normalized)."""
    # Normalize phone number by removing common separators
    normalized = "".join(c for c in phone_number if c.isdigit() or c == "+")
    
    # Try exact match first
    row = await db.fetch_one(
        "SELECT id FROM staff WHERE mobile_phone = %s LIMIT 1",
        (phone_number,)
    )
    if row:
        return int(row["id"])
    
    # Try normalized match
    if normalized != phone_number:
        row = await db.fetch_one(
            "SELECT id FROM staff WHERE mobile_phone = %s LIMIT 1",
            (normalized,)
        )
        if row:
            return int(row["id"])
    
    # Try partial match (last 10 digits)
    if len(normalized) >= 10:
        last_digits = normalized[-10:]
        row = await db.fetch_one(
            "SELECT id FROM staff WHERE mobile_phone LIKE %s LIMIT 1",
            (f"%{last_digits}",)
        )
        if row:
            return int(row["id"])
    
    return None
