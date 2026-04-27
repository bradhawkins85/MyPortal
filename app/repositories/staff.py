from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Iterable, List, Sequence

from app.core.database import db
from app.repositories import staff_custom_fields as staff_custom_fields_repo


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
    if isinstance(value, date):
        dt = datetime.combine(value, time.min, tzinfo=timezone.utc)
        return dt.replace(tzinfo=None)
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


def _serialize_datetime(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = _ensure_utc(value)
    else:
        dt = _coerce_datetime(value)
        if dt is None:
            return None
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _decode_staff_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    if not cursor:
        return None
    raw_cursor = str(cursor).strip()
    if not raw_cursor:
        return None
    updated_part, separator, id_part = raw_cursor.rpartition("|")
    if separator != "|" or not updated_part:
        return None
    updated_at = _coerce_datetime(updated_part)
    if updated_at is None:
        return None
    try:
        staff_id = int(id_part)
    except (TypeError, ValueError):
        return None
    return (updated_at, staff_id)


def _map_staff_row(row: dict[str, Any]) -> dict[str, Any]:
    mapped = dict(row)
    mapped["enabled"] = bool(int(mapped.get("enabled", 0)))
    mapped["is_ex_staff"] = bool(int(mapped.get("is_ex_staff", 0)))
    if "company_id" in mapped and mapped["company_id"] is not None:
        mapped["company_id"] = int(mapped["company_id"])
    if "verification_code" in mapped and mapped["verification_code"] is None:
        mapped["verification_code"] = None
    mapped["date_onboarded"] = _serialize_datetime(mapped.get("date_onboarded"))
    mapped["date_offboarded"] = _serialize_datetime(mapped.get("date_offboarded"))
    mapped["onboarding_completed_at"] = _serialize_datetime(mapped.get("onboarding_completed_at"))
    mapped["requested_at"] = _serialize_datetime(mapped.get("requested_at"))
    mapped["approved_at"] = _serialize_datetime(mapped.get("approved_at"))
    mapped["m365_last_sign_in"] = _serialize_datetime(mapped.get("m365_last_sign_in"))
    mapped["created_at"] = _serialize_datetime(mapped.get("created_at"))
    mapped["updated_at"] = _serialize_datetime(mapped.get("updated_at"))
    mapped["onboarding_complete"] = bool(int(mapped.get("onboarding_complete", 0)))
    mapped["approval_status"] = str(mapped.get("approval_status") or "pending")
    return mapped


async def count_staff(
    company_id: int,
    *,
    enabled: bool | None = None,
    exclude_package_staff: bool = False,
) -> int:
    conditions = ["company_id = %s"]
    params: list[Any] = [company_id]
    if enabled is not None:
        conditions.append("enabled = %s")
        params.append(1 if enabled else 0)
    if exclude_package_staff:
        conditions.append("NOT (LOWER(SUBSTR(email, 1, 8)) = 'package_')")
    where_clause = " AND ".join(conditions)
    row = await db.fetch_one(
        f"SELECT COUNT(*) AS count FROM staff WHERE {where_clause}",
        tuple(params),
    )
    return int(row["count"]) if row else 0


async def list_staff(
    company_id: int,
    *,
    enabled: bool | None = None,
    exclude_ex_staff: bool = False,
    exclude_package_staff: bool = False,
    onboarding_complete: bool | None = None,
    onboarding_status: str | None = None,
    offboarding_complete: bool | None = None,
    offboarding_status: str | None = None,
    created_after: datetime | None = None,
    updated_after: datetime | None = None,
    offboarding_requested_after: datetime | None = None,
    offboarding_updated_after: datetime | None = None,
    scheduled_from: datetime | None = None,
    scheduled_to: datetime | None = None,
    due_only: bool = False,
    cursor: str | None = None,
    page_size: int | None = None,
) -> list[dict[str, Any]]:
    conditions = ["s.company_id = %s"]
    params: list[Any] = [company_id]
    if enabled is not None:
        conditions.append("s.enabled = %s")
        params.append(1 if enabled else 0)
    if exclude_ex_staff:
        conditions.append("s.is_ex_staff = 0")
    if exclude_package_staff:
        conditions.append("NOT (LOWER(SUBSTR(s.email, 1, 8)) = 'package_')")
    if onboarding_complete is not None:
        conditions.append("s.onboarding_complete = %s")
        params.append(1 if onboarding_complete else 0)
    if onboarding_status:
        conditions.append("LOWER(s.onboarding_status) = LOWER(%s)")
        params.append(str(onboarding_status).strip())
    if offboarding_complete is not None:
        if offboarding_complete:
            conditions.append("LOWER(s.onboarding_status) = 'offboarding_completed'")
        else:
            conditions.append("LOWER(s.onboarding_status) LIKE 'offboarding_%'")
            conditions.append("LOWER(s.onboarding_status) <> 'offboarding_completed'")
    if offboarding_status:
        clean_offboarding_status = str(offboarding_status).strip().lower()
        if clean_offboarding_status.startswith("offboarding_"):
            conditions.append("LOWER(s.onboarding_status) = %s")
            params.append(clean_offboarding_status)
        else:
            conditions.append("LOWER(s.onboarding_status) = %s")
            params.append(f"offboarding_{clean_offboarding_status}")
    if created_after is not None:
        conditions.append("s.created_at > %s")
        params.append(_coerce_datetime(created_after))
    if updated_after is not None:
        conditions.append("s.updated_at > %s")
        params.append(_coerce_datetime(updated_after))
    if offboarding_requested_after is not None:
        conditions.append("s.date_offboarded > %s")
        params.append(_coerce_datetime(offboarding_requested_after))
    if offboarding_updated_after is not None:
        conditions.append("s.updated_at > %s")
        params.append(_coerce_datetime(offboarding_updated_after))
        conditions.append("LOWER(s.onboarding_status) LIKE 'offboarding_%'")
    if scheduled_from is not None:
        conditions.append("e.scheduled_for_utc >= %s")
        params.append(_coerce_datetime(scheduled_from))
    if scheduled_to is not None:
        conditions.append("e.scheduled_for_utc <= %s")
        params.append(_coerce_datetime(scheduled_to))
    if due_only:
        conditions.append("e.scheduled_for_utc IS NOT NULL")
        conditions.append("e.scheduled_for_utc <= %s")
        conditions.append("LOWER(COALESCE(e.state, '')) IN ('approved', 'offboarding_approved')")
        params.append(datetime.now(timezone.utc).replace(tzinfo=None))
    decoded_cursor = _decode_staff_cursor(cursor)
    if decoded_cursor is not None:
        cursor_updated_at, cursor_staff_id = decoded_cursor
        conditions.append(
            "(s.updated_at > %s OR (s.updated_at = %s AND s.id > %s))"
        )
        params.extend([cursor_updated_at, cursor_updated_at, cursor_staff_id])
    where_clause = " AND ".join(conditions)
    if page_size is None:
        page_size = 200
    safe_page_size = max(1, min(int(page_size), 500))
    rows = await db.fetch_all(
        """
        SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name
        FROM staff AS s
        LEFT JOIN staff_verification_codes AS svc ON svc.staff_id = s.id
        LEFT JOIN (
            SELECT e1.* FROM staff_onboarding_workflow_executions AS e1
            INNER JOIN (
                SELECT staff_id, MAX(id) AS max_id
                FROM staff_onboarding_workflow_executions
                GROUP BY staff_id
            ) AS latest ON e1.id = latest.max_id
        ) AS e ON e.staff_id = s.id
        WHERE {where}
        ORDER BY s.updated_at ASC, s.id ASC
        LIMIT %s
        """.format(where=where_clause),
        tuple([*params, safe_page_size]),
    )
    mapped_rows = [_map_staff_row(row) for row in rows]
    if not mapped_rows:
        return mapped_rows
    values_by_staff = await staff_custom_fields_repo.get_all_staff_field_values(
        company_id,
        [int(row["id"]) for row in mapped_rows if row.get("id") is not None],
    )
    for row in mapped_rows:
        row["custom_fields"] = values_by_staff.get(int(row["id"]), {})
    return mapped_rows


async def list_enabled_staff_users(company_id: int) -> List[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT DISTINCT u.*
        FROM staff AS s
        INNER JOIN users AS u
            ON LOWER(u.email) = LOWER(s.email)
        WHERE s.company_id = %s
          AND s.enabled = 1
          AND u.company_id = s.company_id
        ORDER BY LOWER(u.email), u.id
        """,
        (company_id,),
    )
    return [dict(row) for row in rows]


async def list_staff_with_users(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            s.id AS staff_id,
            s.first_name,
            s.last_name,
            s.email,
            s.enabled,
            u.id AS user_id
        FROM staff AS s
        LEFT JOIN users AS u
            ON u.company_id = s.company_id
           AND LOWER(u.email) = LOWER(s.email)
        WHERE s.company_id = %s
        ORDER BY s.last_name, s.first_name, s.email
        """,
        (company_id,),
    )
    results: list[dict[str, Any]] = []
    for row in rows:
        staff_id = row.get("staff_id")
        email = (row.get("email") or "").strip()
        if staff_id is None or not email:
            continue
        entry: dict[str, Any] = {
            "staff_id": int(staff_id),
            "first_name": (row.get("first_name") or "").strip(),
            "last_name": (row.get("last_name") or "").strip(),
            "email": email,
            "enabled": bool(row.get("enabled")),
        }
        user_id = row.get("user_id")
        if user_id is not None:
            try:
                entry["user_id"] = int(user_id)
            except (TypeError, ValueError):
                entry["user_id"] = None
        else:
            entry["user_id"] = None
        results.append(entry)
    return results


async def list_all_staff(
    *,
    account_action: str | None = None,
    email: str | None = None,
    scheduled_from: datetime | None = None,
    scheduled_to: datetime | None = None,
    due_only: bool = False,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if account_action:
        conditions.append("s.account_action = %s")
        params.append(account_action)
    if email:
        conditions.append("LOWER(s.email) LIKE %s")
        params.append(f"%{email.lower()}%")
    if scheduled_from is not None:
        conditions.append("e.scheduled_for_utc >= %s")
        params.append(_coerce_datetime(scheduled_from))
    if scheduled_to is not None:
        conditions.append("e.scheduled_for_utc <= %s")
        params.append(_coerce_datetime(scheduled_to))
    if due_only:
        conditions.append("e.scheduled_for_utc IS NOT NULL")
        conditions.append("e.scheduled_for_utc <= %s")
        conditions.append("LOWER(COALESCE(e.state, '')) IN ('approved', 'offboarding_approved')")
        params.append(datetime.now(timezone.utc).replace(tzinfo=None))
    where_clause = " AND ".join(conditions)
    sql = """
        SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name
        FROM staff AS s
        LEFT JOIN staff_verification_codes AS svc ON svc.staff_id = s.id
        LEFT JOIN (
            SELECT e1.* FROM staff_onboarding_workflow_executions AS e1
            INNER JOIN (
                SELECT staff_id, MAX(id) AS max_id
                FROM staff_onboarding_workflow_executions
                GROUP BY staff_id
            ) AS latest ON e1.id = latest.max_id
        ) AS e ON e.staff_id = s.id
    """
    if where_clause:
        sql += f" WHERE {where_clause}"
    sql += " ORDER BY s.company_id, s.last_name, s.first_name"
    rows = await db.fetch_all(sql, tuple(params))
    mapped_rows = [_map_staff_row(row) for row in rows]
    if not mapped_rows:
        return mapped_rows
    grouped_by_company: dict[int, list[int]] = {}
    for row in mapped_rows:
        company_id = row.get("company_id")
        staff_id = row.get("id")
        if company_id is None or staff_id is None:
            continue
        grouped_by_company.setdefault(int(company_id), []).append(int(staff_id))
    values_by_staff: dict[int, dict[str, Any]] = {}
    for company_id, staff_ids in grouped_by_company.items():
        company_values = await staff_custom_fields_repo.get_all_staff_field_values(
            company_id,
            staff_ids,
        )
        values_by_staff.update(company_values)
    for row in mapped_rows:
        row["custom_fields"] = values_by_staff.get(int(row["id"]), {})
    return mapped_rows


async def list_all_staff_for_import(company_id: int) -> list[dict[str, Any]]:
    """Return all staff rows for a company without pagination.

    Intended only for internal import/sync operations where every existing
    record must be considered to avoid creating duplicates.
    """
    rows = await db.fetch_all(
        """
        SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name
        FROM staff AS s
        LEFT JOIN staff_verification_codes AS svc ON svc.staff_id = s.id
        WHERE s.company_id = %s
        ORDER BY s.id ASC
        """,
        (company_id,),
    )
    return [_map_staff_row(row) for row in rows]


async def list_staff_by_email(email: str) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name
        FROM staff AS s
        LEFT JOIN staff_verification_codes AS svc ON svc.staff_id = s.id
        WHERE LOWER(s.email) = LOWER(%s)
        """,
        (email,),
    )
    return [_map_staff_row(row) for row in rows]


async def list_departments(company_id: int) -> list[str]:
    rows = await db.fetch_all(
        """
        SELECT DISTINCT department FROM staff
        WHERE company_id = %s AND department IS NOT NULL AND department <> ''
        ORDER BY department
        """,
        (company_id,),
    )
    departments = [row["department"] for row in rows if row.get("department")]
    return [str(dept) for dept in departments]


async def get_staff_by_id(staff_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name
        FROM staff AS s
        LEFT JOIN staff_verification_codes AS svc ON svc.staff_id = s.id
        WHERE s.id = %s
        """,
        (staff_id,),
    )
    if not row:
        return None
    mapped = _map_staff_row(row)
    company_id = mapped.get("company_id")
    if company_id is not None:
        values_by_staff = await staff_custom_fields_repo.get_all_staff_field_values(
            int(company_id), [staff_id]
        )
        mapped["custom_fields"] = values_by_staff.get(staff_id, {})
    else:
        mapped["custom_fields"] = {}
    return mapped


async def get_staff_by_company_and_email(
    company_id: int, email: str
) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name
        FROM staff AS s
        LEFT JOIN staff_verification_codes AS svc ON svc.staff_id = s.id
        WHERE s.company_id = %s AND LOWER(s.email) = LOWER(%s)
        """,
        (company_id, email),
    )
    return _map_staff_row(row) if row else None


async def create_staff(
    *,
    company_id: int,
    first_name: str,
    last_name: str,
    email: str,
    mobile_phone: str | None = None,
    date_onboarded: datetime | None = None,
    date_offboarded: datetime | None = None,
    enabled: bool = True,
    is_ex_staff: bool = False,
    street: str | None = None,
    city: str | None = None,
    state: str | None = None,
    postcode: str | None = None,
    country: str | None = None,
    department: str | None = None,
    job_title: str | None = None,
    org_company: str | None = None,
    manager_name: str | None = None,
    account_action: str | None = None,
    syncro_contact_id: str | None = None,
    source: str = "manual",
    m365_last_sign_in: datetime | None = None,
    onboarding_status: str = "requested",
    onboarding_complete: bool = False,
    onboarding_completed_at: datetime | None = None,
    approval_status: str = "pending",
    requested_by_user_id: int | None = None,
    requested_at: datetime | None = None,
    approved_by_user_id: int | None = None,
    approved_at: datetime | None = None,
    request_notes: str | None = None,
    approval_notes: str | None = None,
) -> dict[str, Any]:
    staff_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO staff (
            company_id,
            first_name,
            last_name,
            email,
            mobile_phone,
            date_onboarded,
            date_offboarded,
            enabled,
            is_ex_staff,
            street,
            city,
            state,
            postcode,
            country,
            department,
            job_title,
            org_company,
            manager_name,
            account_action,
            syncro_contact_id,
            source,
            m365_last_sign_in,
            onboarding_status,
            onboarding_complete,
            onboarding_completed_at,
            approval_status,
            requested_by_user_id,
            requested_at,
            approved_by_user_id,
            approved_at,
            request_notes,
            approval_notes
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            company_id,
            first_name,
            last_name,
            email,
            mobile_phone,
            _coerce_datetime(date_onboarded),
            _coerce_datetime(date_offboarded),
            1 if enabled else 0,
            1 if is_ex_staff else 0,
            street,
            city,
            state,
            postcode,
            country,
            department,
            job_title,
            org_company,
            manager_name,
            account_action,
            syncro_contact_id,
            source,
            _coerce_datetime(m365_last_sign_in),
            onboarding_status,
            1 if onboarding_complete else 0,
            _coerce_datetime(onboarding_completed_at),
            approval_status,
            requested_by_user_id,
            _coerce_datetime(requested_at),
            approved_by_user_id,
            _coerce_datetime(approved_at),
            request_notes,
            approval_notes,
        ),
    )
    if not staff_id:
        raise RuntimeError("Failed to create staff record")
    created = await db.fetch_one("SELECT * FROM staff WHERE id = %s", (staff_id,))
    if not created:
        raise RuntimeError("Failed to retrieve created staff record")
    return _map_staff_row(created)


async def update_staff(
    staff_id: int,
    *,
    company_id: int,
    first_name: str,
    last_name: str,
    email: str | None,
    mobile_phone: str | None,
    date_onboarded: datetime | None,
    date_offboarded: datetime | None,
    enabled: bool,
    is_ex_staff: bool = False,
    street: str | None,
    city: str | None,
    state: str | None,
    postcode: str | None,
    country: str | None,
    department: str | None,
    job_title: str | None,
    org_company: str | None,
    manager_name: str | None,
    account_action: str | None,
    syncro_contact_id: str | None,
    onboarding_status: str | None = None,
    onboarding_complete: bool | None = None,
    onboarding_completed_at: datetime | None = None,
    approval_status: str | None = None,
    requested_by_user_id: int | None = None,
    requested_at: datetime | None = None,
    approved_by_user_id: int | None = None,
    approved_at: datetime | None = None,
    request_notes: str | None = None,
    approval_notes: str | None = None,
    m365_last_sign_in: datetime | None = None,
    offboarding_out_of_office: str | None = None,
    offboarding_email_forward_to: str | None = None,
    offboarding_mailbox_grant_emails: str | None = None,
) -> dict[str, Any]:
    await db.execute(
        """
        UPDATE staff
        SET
            company_id = %s,
            first_name = %s,
            last_name = %s,
            email = %s,
            mobile_phone = %s,
            date_onboarded = %s,
            date_offboarded = %s,
            enabled = %s,
            is_ex_staff = %s,
            street = %s,
            city = %s,
            state = %s,
            postcode = %s,
            country = %s,
            department = %s,
            job_title = %s,
            org_company = %s,
            manager_name = %s,
            account_action = %s,
            syncro_contact_id = %s,
            onboarding_status = COALESCE(%s, onboarding_status),
            onboarding_complete = COALESCE(%s, onboarding_complete),
            onboarding_completed_at = COALESCE(%s, onboarding_completed_at),
            approval_status = COALESCE(%s, approval_status),
            requested_by_user_id = COALESCE(%s, requested_by_user_id),
            requested_at = COALESCE(%s, requested_at),
            approved_by_user_id = COALESCE(%s, approved_by_user_id),
            approved_at = COALESCE(%s, approved_at),
            request_notes = COALESCE(%s, request_notes),
            approval_notes = COALESCE(%s, approval_notes),
            m365_last_sign_in = COALESCE(%s, m365_last_sign_in),
            offboarding_out_of_office = COALESCE(%s, offboarding_out_of_office),
            offboarding_email_forward_to = COALESCE(%s, offboarding_email_forward_to),
            offboarding_mailbox_grant_emails = COALESCE(%s, offboarding_mailbox_grant_emails)
        WHERE id = %s
        """,
        (
            company_id,
            first_name,
            last_name,
            email,
            mobile_phone,
            _coerce_datetime(date_onboarded),
            _coerce_datetime(date_offboarded),
            1 if enabled else 0,
            1 if is_ex_staff else 0,
            street,
            city,
            state,
            postcode,
            country,
            department,
            job_title,
            org_company,
            manager_name,
            account_action,
            syncro_contact_id,
            onboarding_status,
            (1 if onboarding_complete else 0) if onboarding_complete is not None else None,
            _coerce_datetime(onboarding_completed_at),
            approval_status,
            requested_by_user_id,
            _coerce_datetime(requested_at),
            approved_by_user_id,
            _coerce_datetime(approved_at),
            request_notes,
            approval_notes,
            _coerce_datetime(m365_last_sign_in),
            offboarding_out_of_office,
            offboarding_email_forward_to,
            offboarding_mailbox_grant_emails,
            staff_id,
        ),
    )
    updated = await get_staff_by_id(staff_id)
    if not updated:
        raise ValueError("Staff record not found after update")
    return updated


async def reset_staff_onboarding_status(staff_id: int, *, onboarding_status: str) -> None:
    """Reset onboarding-related fields for a staff record."""
    await db.execute(
        """
        UPDATE staff
        SET onboarding_status = %s,
            onboarding_complete = 0,
            onboarding_completed_at = NULL
        WHERE id = %s
        """,
        (onboarding_status, staff_id),
    )


async def delete_staff(staff_id: int) -> None:
    await db.execute("DELETE FROM staff_licenses WHERE staff_id = %s", (staff_id,))
    await db.execute("DELETE FROM staff WHERE id = %s", (staff_id,))


async def update_m365_last_sign_in(staff_id: int, last_sign_in: datetime | None) -> None:
    """Update only the m365_last_sign_in field for a staff record."""
    await db.execute(
        "UPDATE staff SET m365_last_sign_in = %s WHERE id = %s",
        (_coerce_datetime(last_sign_in), staff_id),
    )


async def delete_m365_staff_not_in(company_id: int, keep_emails: set[str]) -> int:
    """Delete M365-sourced staff for the company whose emails are not in *keep_emails*.

    Returns the number of records deleted.
    """
    rows = await db.fetch_all(
        "SELECT id, email FROM staff WHERE company_id = %s AND source = 'm365'",
        (company_id,),
    )
    to_delete = [
        int(row["id"])
        for row in rows
        if (row.get("email") or "").lower() not in keep_emails
    ]
    for staff_id in to_delete:
        await db.execute("DELETE FROM staff_licenses WHERE staff_id = %s", (staff_id,))
        await db.execute("DELETE FROM staff WHERE id = %s", (staff_id,))
    return len(to_delete)


async def list_active_staff_for_offboarding(company_id: int, *, exclude_staff_id: int | None = None) -> list[dict[str, Any]]:
    """Return enabled, non-ex-staff members for the given company.

    Used to populate email-forwarding and mailbox-access dropdowns on the
    offboarding request form. Optionally excludes the staff member being
    offboarded.
    """
    conditions = ["s.company_id = %s", "s.enabled = 1", "s.is_ex_staff = 0"]
    params: list[Any] = [company_id]
    if exclude_staff_id is not None:
        conditions.append("s.id != %s")
        params.append(exclude_staff_id)
    where = " AND ".join(conditions)
    rows = await db.fetch_all(
        f"""
        SELECT s.id, s.first_name, s.last_name, s.email
        FROM staff AS s
        WHERE {where}
        ORDER BY s.last_name, s.first_name, s.email
        """,
        tuple(params),
    )
    return [dict(row) for row in rows]


async def set_enabled(staff_id: int, enabled: bool) -> None:
    await db.execute(
        "UPDATE staff SET enabled = %s WHERE id = %s",
        (1 if enabled else 0, staff_id),
    )


async def upsert_verification_code(
    staff_id: int, *, code: str, admin_name: str | None
) -> None:
    await db.execute(
        """
        INSERT INTO staff_verification_codes (staff_id, code, admin_name, created_at)
        VALUES (%s, %s, %s, UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE
            code = VALUES(code),
            admin_name = VALUES(admin_name),
            created_at = VALUES(created_at)
        """,
        (staff_id, code, admin_name),
    )


async def purge_expired_verification_codes(ttl_minutes: int = 5) -> None:
    await db.execute(
        "DELETE FROM staff_verification_codes WHERE created_at < (UTC_TIMESTAMP() - INTERVAL %s MINUTE)",
        (ttl_minutes,),
    )


async def get_verification_by_code(code: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM staff_verification_codes WHERE code = %s",
        (code,),
    )
    return dict(row) if row else None
