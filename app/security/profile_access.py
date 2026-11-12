"""Access to work details and integrations on the self-service profile."""

from typing import Any, Mapping

from fastapi import HTTPException, Request, status

from app.repositories import user_companies as user_company_repo
from app.security.menu_permissions import menu_has_access, normalize_menu_permissions
from app.security.session import session_manager


def can_edit_profile_technician_tools(
    user: Mapping[str, Any], membership: Mapping[str, Any] | None
) -> bool:
    if user.get("is_super_admin"):
        return True
    if not membership:
        return False
    permissions = normalize_menu_permissions(
        membership.get("menu_permissions") or membership.get("permissions")
    )
    return bool(membership.get("is_admin")) or any(
        menu_has_access(permissions, key, write=True)
        for key in ("menu.admin.company", "menu.admin.technician")
    )


async def require_profile_technician_tools(request: Request, user: Mapping[str, Any]) -> None:
    membership = getattr(request.state, "active_membership", None)
    if not user.get("is_super_admin") and membership is None:
        company_id = getattr(request.state, "active_company_id", None)
        if company_id is None:
            session = await session_manager.load_session(request)
            company_id = session.active_company_id if session else None
        if company_id is None:
            company_id = user.get("company_id")
        if company_id is not None:
            membership = await user_company_repo.get_user_company(int(user["id"]), int(company_id))
    if not can_edit_profile_technician_tools(user, membership):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or technician profile access required",
        )
