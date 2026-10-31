from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, status

from app.repositories import companies as company_repo
from app.repositories import user_companies as user_company_repo
from app.security.menu_permissions import membership_menu_can


async def management_scope(
    user: dict[str, Any],
    *,
    include_archived: bool = False,
    menu_can: Callable[..., bool] | None = None,
) -> tuple[bool, list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """Return the companies ``user`` may manage as ``(is_super_admin, companies, memberships)``."""
    can = menu_can or membership_menu_can

    is_super_admin = bool(user.get("is_super_admin"))
    if is_super_admin:
        companies = await company_repo.list_companies(include_archived=include_archived)
        companies.sort(key=lambda item: (item.get("name") or "").lower())
        return True, companies, {}

    memberships = await user_company_repo.list_companies_for_user(user["id"])
    membership_lookup: dict[int, dict[str, Any]] = {}
    for record in memberships:
        raw_company_id = record.get("company_id")
        try:
            company_id = int(raw_company_id)
        except (TypeError, ValueError):
            continue
        if can(user, record, "menu.admin.company"):
            membership_lookup[company_id] = record

    if not membership_lookup:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    companies: list[dict[str, Any]] = []
    for company_id in sorted(membership_lookup.keys()):
        company = await company_repo.get_company_by_id(company_id)
        if company:
            # Filter archived companies for non-super admins unless explicitly requested
            if include_archived or not company.get("archived"):
                companies.append(company)

    if not companies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    return False, companies, membership_lookup
