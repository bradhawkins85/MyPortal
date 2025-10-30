from __future__ import annotations

from typing import Any, Optional

from app.core.database import db

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


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    normalised = dict(row)
    for key in _BOOLEAN_FIELDS:
        if key in normalised:
            normalised[key] = bool(int(normalised.get(key, 0)))
    if "staff_permission" in normalised and normalised["staff_permission"] is not None:
        normalised["staff_permission"] = int(normalised["staff_permission"])
    if "company_id" in normalised and normalised["company_id"] is not None:
        normalised["company_id"] = int(normalised["company_id"])
    if "staff_id" in normalised and normalised["staff_id"] is not None:
        normalised["staff_id"] = int(normalised["staff_id"])
    if "role_id" in normalised and normalised["role_id"] is not None:
        normalised["role_id"] = int(normalised["role_id"])
    return normalised


async def upsert_assignment(
    *,
    staff_id: int,
    company_id: int,
    staff_permission: int = 0,
    can_manage_staff: bool = False,
    can_manage_licenses: bool = False,
    can_manage_assets: bool = False,
    can_manage_invoices: bool = False,
    can_manage_office_groups: bool = False,
    can_order_licenses: bool = False,
    can_access_shop: bool = False,
    can_access_cart: bool = False,
    can_access_orders: bool = False,
    can_access_forms: bool = False,
    is_admin: bool = False,
    role_id: int | None = None,
) -> dict[str, Any]:
    staff_permission_value = max(0, int(staff_permission))
    if staff_permission_value > 3:
        staff_permission_value = 3
    await db.execute(
        """
        INSERT INTO pending_staff_access (
            staff_id,
            company_id,
            staff_permission,
            can_manage_staff,
            can_manage_licenses,
            can_manage_assets,
            can_manage_invoices,
            can_manage_office_groups,
            can_order_licenses,
            can_access_shop,
            can_access_cart,
            can_access_orders,
            can_access_forms,
            is_admin,
            role_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
            staff_permission = VALUES(staff_permission),
            can_manage_staff = VALUES(can_manage_staff),
            can_manage_licenses = VALUES(can_manage_licenses),
            can_manage_assets = VALUES(can_manage_assets),
            can_manage_invoices = VALUES(can_manage_invoices),
            can_manage_office_groups = VALUES(can_manage_office_groups),
            can_order_licenses = VALUES(can_order_licenses),
            can_access_shop = VALUES(can_access_shop),
            can_access_cart = VALUES(can_access_cart),
            can_access_orders = VALUES(can_access_orders),
            can_access_forms = VALUES(can_access_forms),
            is_admin = VALUES(is_admin),
            role_id = VALUES(role_id)
        """,
        (
            staff_id,
            company_id,
            staff_permission_value,
            1 if (can_manage_staff or staff_permission_value > 0) else 0,
            1 if can_manage_licenses else 0,
            1 if can_manage_assets else 0,
            1 if can_manage_invoices else 0,
            1 if can_manage_office_groups else 0,
            1 if can_order_licenses else 0,
            1 if can_access_shop else 0,
            1 if can_access_cart else 0,
            1 if can_access_orders else 0,
            1 if can_access_forms else 0,
            1 if is_admin else 0,
            role_id,
        ),
    )
    return await get_assignment(staff_id=staff_id, company_id=company_id)


async def get_assignment(*, staff_id: int, company_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        """
        SELECT *
        FROM pending_staff_access
        WHERE staff_id = %s AND company_id = %s
        """,
        (staff_id, company_id),
    )
    return _normalise(row)


async def list_assignments_for_company(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT *
        FROM pending_staff_access
        WHERE company_id = %s
        """,
        (company_id,),
    )
    return [entry for entry in (_normalise(row) for row in rows) if entry]


async def list_assignments_for_staff(staff_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT *
        FROM pending_staff_access
        WHERE staff_id = %s
        """,
        (staff_id,),
    )
    return [entry for entry in (_normalise(row) for row in rows) if entry]


async def delete_assignment(*, staff_id: int, company_id: int) -> None:
    await db.execute(
        "DELETE FROM pending_staff_access WHERE staff_id = %s AND company_id = %s",
        (staff_id, company_id),
    )
