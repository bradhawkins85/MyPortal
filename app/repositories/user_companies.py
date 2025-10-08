from __future__ import annotations

from typing import Any, List, Optional

from app.core.database import db

_BOOLEAN_FIELDS = {
    "can_manage_licenses",
    "can_manage_staff",
    "can_manage_assets",
    "can_manage_invoices",
    "can_manage_office_groups",
    "can_order_licenses",
    "can_access_shop",
    "is_admin",
}


def _normalise(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    for key in _BOOLEAN_FIELDS:
        if key in normalised:
            normalised[key] = bool(int(normalised.get(key, 0)))
    if "staff_permission" in normalised and normalised["staff_permission"] is not None:
        normalised["staff_permission"] = int(normalised["staff_permission"])
    if "company_id" in normalised and normalised["company_id"] is not None:
        normalised["company_id"] = int(normalised["company_id"])
    if "user_id" in normalised and normalised["user_id"] is not None:
        normalised["user_id"] = int(normalised["user_id"])
    return normalised


async def get_user_company(user_id: int, company_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        "SELECT * FROM user_companies WHERE user_id = %s AND company_id = %s",
        (user_id, company_id),
    )
    return _normalise(row) if row else None


async def list_companies_for_user(user_id: int) -> List[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT uc.*, c.name AS company_name, c.syncro_company_id
        FROM user_companies AS uc
        INNER JOIN companies AS c ON c.id = uc.company_id
        WHERE uc.user_id = %s
        ORDER BY c.name
        """,
        (user_id,),
    )
    companies: List[dict[str, Any]] = []
    for row in rows:
        normalised = _normalise(row)
        normalised["company_name"] = row.get("company_name")
        if "syncro_company_id" in row:
            normalised["syncro_company_id"] = row.get("syncro_company_id")
        companies.append(normalised)
    return companies


async def upsert_user_company(
    *,
    user_id: int,
    company_id: int,
    can_manage_staff: bool = False,
    staff_permission: int = 0,
    is_admin: bool = False,
) -> None:
    await db.execute(
        """
        INSERT INTO user_companies (user_id, company_id, can_manage_staff, staff_permission, is_admin)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            can_manage_staff = VALUES(can_manage_staff),
            staff_permission = VALUES(staff_permission),
            is_admin = VALUES(is_admin)
        """,
        (
            user_id,
            company_id,
            1 if can_manage_staff else 0,
            staff_permission,
            1 if is_admin else 0,
        ),
    )
