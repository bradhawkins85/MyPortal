from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Iterable, List, Sequence

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


def _map_staff_row(row: dict[str, Any]) -> dict[str, Any]:
    mapped = dict(row)
    mapped["enabled"] = bool(int(mapped.get("enabled", 0)))
    if "company_id" in mapped and mapped["company_id"] is not None:
        mapped["company_id"] = int(mapped["company_id"])
    if "verification_code" in mapped and mapped["verification_code"] is None:
        mapped["verification_code"] = None
    mapped["date_onboarded"] = _serialize_datetime(mapped.get("date_onboarded"))
    mapped["date_offboarded"] = _serialize_datetime(mapped.get("date_offboarded"))
    return mapped


async def count_staff(company_id: int, *, enabled: bool | None = None) -> int:
    conditions = ["company_id = %s"]
    params: list[Any] = [company_id]
    if enabled is not None:
        conditions.append("enabled = %s")
        params.append(1 if enabled else 0)
    where_clause = " AND ".join(conditions)
    row = await db.fetch_one(
        f"SELECT COUNT(*) AS count FROM staff WHERE {where_clause}",
        tuple(params),
    )
    return int(row["count"]) if row else 0


async def list_staff(
    company_id: int, *, enabled: bool | None = None
) -> list[dict[str, Any]]:
    conditions = ["s.company_id = %s"]
    params: list[Any] = [company_id]
    if enabled is not None:
        conditions.append("s.enabled = %s")
        params.append(1 if enabled else 0)
    where_clause = " AND ".join(conditions)
    rows = await db.fetch_all(
        """
        SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name
        FROM staff AS s
        LEFT JOIN staff_verification_codes AS svc ON svc.staff_id = s.id
        WHERE {where}
        ORDER BY s.last_name, s.first_name
        """.format(where=where_clause),
        tuple(params),
    )
    return [_map_staff_row(row) for row in rows]


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
    *, account_action: str | None = None, email: str | None = None
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if account_action:
        conditions.append("s.account_action = %s")
        params.append(account_action)
    if email:
        conditions.append("LOWER(s.email) LIKE %s")
        params.append(f"%{email.lower()}%")
    where_clause = " AND ".join(conditions)
    sql = """
        SELECT s.*, svc.code AS verification_code, svc.admin_name AS verification_admin_name
        FROM staff AS s
        LEFT JOIN staff_verification_codes AS svc ON svc.staff_id = s.id
    """
    if where_clause:
        sql += f" WHERE {where_clause}"
    sql += " ORDER BY s.company_id, s.last_name, s.first_name"
    rows = await db.fetch_all(sql, tuple(params))
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
    return _map_staff_row(row) if row else None


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
) -> dict[str, Any]:
    await db.execute(
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
            syncro_contact_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
        ),
    )
    created = await db.fetch_one("SELECT * FROM staff WHERE id = LAST_INSERT_ID()")
    if not created:
        raise RuntimeError("Failed to create staff record")
    return _map_staff_row(created)


async def update_staff(
    staff_id: int,
    *,
    company_id: int,
    first_name: str,
    last_name: str,
    email: str,
    mobile_phone: str | None,
    date_onboarded: datetime | None,
    date_offboarded: datetime | None,
    enabled: bool,
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
            syncro_contact_id = %s
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
            staff_id,
        ),
    )
    updated = await get_staff_by_id(staff_id)
    if not updated:
        raise ValueError("Staff record not found after update")
    return updated


async def delete_staff(staff_id: int) -> None:
    await db.execute("DELETE FROM staff WHERE id = %s", (staff_id,))


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
