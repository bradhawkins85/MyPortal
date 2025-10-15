from __future__ import annotations

from contextlib import suppress
from typing import Any, List, Optional

from app.core.database import db
from app.repositories import company_memberships as membership_repo
from app.repositories import roles as role_repo

_BOOLEAN_FIELDS = {
    "can_manage_licenses",
    "can_manage_staff",
    "can_manage_assets",
    "can_manage_invoices",
    "can_manage_office_groups",
    "can_order_licenses",
    "can_access_shop",
    "can_access_cart",
    "can_access_orders",
    "can_access_forms",
    "is_admin",
}

_PERMISSION_FIELDS = {
    "can_manage_licenses",
    "can_manage_office_groups",
    "can_manage_assets",
    "can_manage_invoices",
    "can_order_licenses",
    "can_access_shop",
    "can_access_cart",
    "can_access_orders",
    "can_access_forms",
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


async def _ensure_company_membership(company_id: int, user_id: int) -> None:
    with suppress(Exception):
        membership = await membership_repo.get_membership_by_company_user(company_id, user_id)
        if membership:
            membership_id = membership.get("id")
            if membership_id and membership.get("status") != "active":
                await membership_repo.update_membership(int(membership_id), status="active")
            return

        default_role = await role_repo.get_role_by_name("Member")
        role_id = default_role.get("id") if default_role else None
        if role_id is None:
            roles = await role_repo.list_roles()
            for record in roles:
                candidate_id = record.get("id")
                if candidate_id is not None:
                    role_id = candidate_id
                    break
        if role_id is None:
            return

        await membership_repo.create_membership(
            company_id=company_id,
            user_id=user_id,
            role_id=int(role_id),
            status="active",
        )


async def _suspend_company_membership(company_id: int, user_id: int) -> None:
    with suppress(Exception):
        membership = await membership_repo.get_membership_by_company_user(company_id, user_id)
        if not membership:
            return
        membership_id = membership.get("id")
        if not membership_id:
            return
        if membership.get("status") != "suspended":
            await membership_repo.update_membership(int(membership_id), status="suspended")


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


async def assign_user_to_company(
    *,
    user_id: int,
    company_id: int,
    can_manage_licenses: bool = False,
    can_manage_staff: bool = False,
    staff_permission: int = 0,
    can_manage_office_groups: bool = False,
    can_manage_assets: bool = False,
    can_manage_invoices: bool = False,
    can_order_licenses: bool = False,
    can_access_shop: bool = False,
    can_access_cart: bool = False,
    can_access_orders: bool = False,
    can_access_forms: bool = False,
    is_admin: bool = False,
) -> None:
    staff_permission_value = max(0, int(staff_permission))
    if staff_permission_value > 3:
        staff_permission_value = 3
    can_manage_staff_flag = 1 if (can_manage_staff or staff_permission_value > 0) else 0
    await db.execute(
        """
        INSERT INTO user_companies (
            user_id,
            company_id,
            can_manage_licenses,
            can_manage_staff,
            staff_permission,
            can_manage_office_groups,
            can_manage_assets,
            can_manage_invoices,
            can_order_licenses,
            can_access_shop,
            can_access_cart,
            can_access_orders,
            can_access_forms,
            is_admin
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            can_manage_licenses = VALUES(can_manage_licenses),
            can_manage_staff = VALUES(can_manage_staff),
            staff_permission = VALUES(staff_permission),
            can_manage_office_groups = VALUES(can_manage_office_groups),
            can_manage_assets = VALUES(can_manage_assets),
            can_manage_invoices = VALUES(can_manage_invoices),
            can_order_licenses = VALUES(can_order_licenses),
            can_access_shop = VALUES(can_access_shop),
            can_access_cart = VALUES(can_access_cart),
            can_access_orders = VALUES(can_access_orders),
            can_access_forms = VALUES(can_access_forms),
            is_admin = VALUES(is_admin)
        """,
        (
            user_id,
            company_id,
            1 if can_manage_licenses else 0,
            can_manage_staff_flag,
            staff_permission_value,
            1 if can_manage_office_groups else 0,
            1 if can_manage_assets else 0,
            1 if can_manage_invoices else 0,
            1 if can_order_licenses else 0,
            1 if can_access_shop else 0,
            1 if can_access_cart else 0,
            1 if can_access_orders else 0,
            1 if can_access_forms else 0,
            1 if is_admin else 0,
        ),
    )
    await _ensure_company_membership(company_id, user_id)


async def upsert_user_company(
    *,
    user_id: int,
    company_id: int,
    can_manage_staff: bool = False,
    staff_permission: int = 0,
    is_admin: bool = False,
) -> None:
    await assign_user_to_company(
        user_id=user_id,
        company_id=company_id,
        can_manage_staff=can_manage_staff,
        staff_permission=staff_permission,
        is_admin=is_admin,
    )


async def list_assignments(company_id: int | None = None) -> List[dict[str, Any]]:
    sql = """
        SELECT
            uc.*,
            u.email,
            u.first_name,
            u.last_name,
            c.name AS company_name,
            c.is_vip,
            m.id AS membership_id,
            m.role_id AS membership_role_id,
            r.name AS membership_role_name
        FROM user_companies AS uc
        INNER JOIN users AS u ON u.id = uc.user_id
        INNER JOIN companies AS c ON c.id = uc.company_id
        LEFT JOIN company_memberships AS m
            ON m.company_id = uc.company_id AND m.user_id = uc.user_id
        LEFT JOIN roles AS r ON r.id = m.role_id
    """
    params: list[Any] = []
    if company_id is not None:
        sql += " WHERE uc.company_id = %s"
        params.append(company_id)
    sql += " ORDER BY u.email, c.name"
    rows = await db.fetch_all(sql, tuple(params))
    assignments: List[dict[str, Any]] = []
    for row in rows:
        normalised = _normalise(row)
        normalised["email"] = row.get("email")
        normalised["first_name"] = row.get("first_name")
        normalised["last_name"] = row.get("last_name")
        normalised["company_name"] = row.get("company_name")
        normalised["membership_id"] = (
            int(row["membership_id"])
            if row.get("membership_id") is not None
            else None
        )
        normalised["membership_role_id"] = (
            int(row["membership_role_id"])
            if row.get("membership_role_id") is not None
            else None
        )
        normalised["membership_role_name"] = row.get("membership_role_name")
        if "is_vip" in row and row.get("is_vip") is not None:
            try:
                normalised["is_vip"] = bool(int(row.get("is_vip")))
            except (TypeError, ValueError):
                normalised["is_vip"] = False
        assignments.append(normalised)
    return assignments


async def update_permission(
    *, user_id: int, company_id: int, field: str, value: bool
) -> None:
    if field not in _PERMISSION_FIELDS:
        raise ValueError(f"Unsupported permission field: {field}")
    await db.execute(
        f"UPDATE user_companies SET {field} = %s WHERE user_id = %s AND company_id = %s",
        (1 if value else 0, user_id, company_id),
    )


async def update_staff_permission(
    *, user_id: int, company_id: int, permission: int
) -> None:
    permission_value = max(0, int(permission))
    if permission_value > 3:
        permission_value = 3
    await db.execute(
        """
        UPDATE user_companies
        SET staff_permission = %s, can_manage_staff = %s
        WHERE user_id = %s AND company_id = %s
        """,
        (
            permission_value,
            1 if permission_value > 0 else 0,
            user_id,
            company_id,
        ),
    )


async def remove_assignment(*, user_id: int, company_id: int) -> None:
    await db.execute(
        "DELETE FROM user_companies WHERE user_id = %s AND company_id = %s",
        (user_id, company_id),
    )
    await _suspend_company_membership(company_id, user_id)
