from __future__ import annotations

from typing import Any

from app.repositories import company_memberships as membership_repo
from app.repositories import pending_staff_access as pending_repo
from app.repositories import staff as staff_repo
from app.repositories import user_companies as user_company_repo


async def apply_pending_access_for_user(user: dict[str, Any]) -> None:
    email = (user.get("email") or "").strip()
    if not email:
        return
    try:
        user_id = int(user.get("id"))
    except (TypeError, ValueError):
        return

    staff_rows = await staff_repo.list_staff_by_email(email)
    if not staff_rows:
        return

    for staff_record in staff_rows:
        staff_id = staff_record.get("id")
        company_id = staff_record.get("company_id")
        if staff_id is None or company_id is None:
            continue
        try:
            staff_id_int = int(staff_id)
            company_id_int = int(company_id)
        except (TypeError, ValueError):
            continue

        assignment = await pending_repo.get_assignment(
            staff_id=staff_id_int,
            company_id=company_id_int,
        )
        if not assignment:
            continue

        await user_company_repo.assign_user_to_company(
            user_id=user_id,
            company_id=company_id_int,
            staff_permission=assignment.get("staff_permission", 0),
            can_manage_staff=assignment.get("can_manage_staff", False),
            can_manage_licenses=assignment.get("can_manage_licenses", False),
            can_manage_assets=assignment.get("can_manage_assets", False),
            can_manage_invoices=assignment.get("can_manage_invoices", False),
            can_manage_office_groups=assignment.get("can_manage_office_groups", False),
            can_order_licenses=assignment.get("can_order_licenses", False),
            can_access_shop=assignment.get("can_access_shop", False),
            can_access_cart=assignment.get("can_access_cart", False),
            can_access_orders=assignment.get("can_access_orders", False),
            can_access_forms=assignment.get("can_access_forms", False),
            is_admin=assignment.get("is_admin", False),
        )

        role_id_raw = assignment.get("role_id")
        role_id_int: int | None
        if role_id_raw is None:
            role_id_int = None
        else:
            try:
                role_id_int = int(role_id_raw)
            except (TypeError, ValueError):
                role_id_int = None

        if role_id_int is not None:
            membership = await membership_repo.get_membership_by_company_user(
                company_id_int, user_id
            )
            if membership:
                membership_id = membership.get("id")
                if membership_id and membership.get("role_id") != role_id_int:
                    await membership_repo.update_membership(
                        int(membership_id), role_id=role_id_int
                    )

        await pending_repo.delete_assignment(
            staff_id=staff_id_int, company_id=company_id_int
        )
