from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.repositories import users as users_repo
from app.security.menu_permissions import normalize_menu_permissions


def can_assign_invoice(user: dict[str, Any], membership: dict[str, Any] | None) -> bool:
    permissions = normalize_menu_permissions((membership or {}).get("menu_permissions"))
    return bool(
        user.get("is_super_admin") or (membership or {}).get("is_admin")
        or permissions["menu.admin.company"] == "write"
    )


async def validate_invoice_assignee(company_id: int, assigned_user_id: int | None) -> None:
    if assigned_user_id is None:
        return
    members = await users_repo.list_company_members(company_id)
    if not any(int(member["id"]) == assigned_user_id for member in members):
        raise HTTPException(status_code=400, detail="Invoice assignee must be a member of the company")
