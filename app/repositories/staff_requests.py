from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db


def _coerce_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _map_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    raw_cf = data.pop("custom_fields_json", None)
    if isinstance(raw_cf, str) and raw_cf.strip():
        try:
            data["custom_fields"] = json.loads(raw_cf)
        except (ValueError, TypeError):
            data["custom_fields"] = {}
    else:
        data["custom_fields"] = {}
    return data


async def create_request(
    *,
    company_id: int,
    first_name: str,
    last_name: str,
    email: str | None = None,
    mobile_phone: str | None = None,
    date_onboarded: datetime | None = None,
    department: str | None = None,
    job_title: str | None = None,
    request_notes: str | None = None,
    custom_fields: dict[str, Any] | None = None,
    requested_by_user_id: int | None = None,
    requested_at: datetime | None = None,
) -> dict[str, Any]:
    custom_fields_json = json.dumps(custom_fields) if custom_fields else None
    request_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO staff_requests (
            company_id, first_name, last_name, email, mobile_phone,
            date_onboarded, department, job_title, request_notes,
            custom_fields_json, status, requested_by_user_id, requested_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
        """,
        (
            company_id,
            first_name,
            last_name,
            email,
            mobile_phone,
            _coerce_datetime(date_onboarded),
            department,
            job_title,
            request_notes,
            custom_fields_json,
            requested_by_user_id,
            _coerce_datetime(requested_at) or datetime.now(timezone.utc).replace(tzinfo=None),
        ),
    )
    row = await get_request_by_id(request_id)
    return row or {"id": request_id, "company_id": company_id}


async def get_request_by_id(request_id: int) -> dict[str, Any] | None:
    rows = await db.fetch_all(
        "SELECT * FROM staff_requests WHERE id = %s",
        (request_id,),
    )
    if not rows:
        return None
    return _map_row(rows[0])


async def list_requests(
    company_id: int,
    *,
    status: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["company_id = %s"]
    params: list[Any] = [company_id]
    if status is not None:
        conditions.append("LOWER(status) = LOWER(%s)")
        params.append(str(status).strip())
    where = " AND ".join(conditions)
    rows = await db.fetch_all(
        f"SELECT * FROM staff_requests WHERE {where} ORDER BY created_at DESC",
        tuple(params),
    )
    return [_map_row(row) for row in rows]


async def update_request_status(
    request_id: int,
    *,
    status: str,
    approved_by_user_id: int | None = None,
    approved_at: datetime | None = None,
    approval_notes: str | None = None,
    staff_id: int | None = None,
) -> dict[str, Any] | None:
    await db.execute(
        """
        UPDATE staff_requests
        SET status = %s,
            approved_by_user_id = %s,
            approved_at = %s,
            approval_notes = COALESCE(%s, approval_notes),
            staff_id = COALESCE(%s, staff_id),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (
            status,
            approved_by_user_id,
            _coerce_datetime(approved_at),
            approval_notes,
            staff_id,
            request_id,
        ),
    )
    return await get_request_by_id(request_id)


async def delete_request(request_id: int) -> None:
    await db.execute(
        "DELETE FROM staff_requests WHERE id = %s",
        (request_id,),
    )
