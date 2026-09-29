"""
Role-Based Access Control dependencies for Business Continuity (BC) system.

Provides authentication and authorization dependencies for BC5 API endpoints.
Implements viewer, editor, approver, and admin roles.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request, status

from app.api.dependencies.auth import get_current_session, get_current_user
from app.repositories import company_memberships as membership_repo
from app.repositories import user_permissions as user_permissions_repo
from app.schemas.bc5_models import BCUserRole
from app.security.session import SessionData


# Permission keys for BC system
BC_VIEWER_PERMISSION = "bc.viewer"
BC_EDITOR_PERMISSION = "bc.editor"
BC_APPROVER_PERMISSION = "bc.approver"
BC_ADMIN_PERMISSION = "bc.admin"


async def user_has_company_permission(
    user_id: int,
    company_id: int | None,
    permission: str,
) -> bool:
    """Return whether the user holds ``permission`` in ``company_id`` specifically.

    Unlike :func:`membership_repo.user_has_permission`, which grants access when
    *any* of the user's memberships carries the permission, this evaluates only
    the active membership for the given company (role permissions plus per-user
    grants for that company). Super admins keep full access, and a Super Admin
    role simulation stays authoritative.
    """
    from app.repositories import users as user_repo
    from app.services.role_switching import effective_role_has_permission

    simulated_decision = effective_role_has_permission(permission)
    if simulated_decision is not None:
        return simulated_decision

    user_record = await user_repo.get_user_by_id(user_id)
    if user_record and bool(user_record.get("is_super_admin")):
        return True

    if company_id is None:
        return False

    membership = await membership_repo.get_membership_by_company_user(company_id, user_id)
    if not membership or membership.get("status") != "active":
        return False
    if permission in (membership.get("legacy_permissions") or []):
        return True

    user_permissions = await user_permissions_repo.list_user_permissions(user_id, company_id)
    return membership_repo._permission_matches(user_permissions, permission)


async def get_active_company_id(
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> int | None:
    """Resolve the caller's active company for company-scoped permission checks."""
    active_company_id = getattr(request.state, "active_company_id", None)
    if active_company_id is None:
        active_company_id = getattr(session, "active_company_id", None)
    return active_company_id


def _coerce_company_id(company_id: Any) -> int | None:
    # Direct (non-FastAPI) calls may leave the Depends() marker as the value.
    if isinstance(company_id, bool) or not isinstance(company_id, int):
        return None
    return company_id


async def _check_bc_permission(
    user: dict,
    permission_key: str,
    company_id: int | None = None,
) -> bool:
    """Check if user has a specific BC permission in the active company."""
    if user.get("is_super_admin"):
        return True
    
    user_id = user.get("id")
    if not user_id:
        return False
    
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return False
    
    if company_id is None:
        company_id = _coerce_company_id(user.get("bc_active_company_id"))
    
    try:
        return await user_has_company_permission(user_id_int, company_id, permission_key)
    except Exception:
        return False


async def _get_user_bc_role(user: dict, company_id: int | None = None) -> BCUserRole | None:
    """Determine the highest BC role for a user in the active company."""
    if user.get("is_super_admin"):
        return BCUserRole.ADMIN
    
    # Check roles in descending order of privilege
    if await _check_bc_permission(user, BC_ADMIN_PERMISSION, company_id):
        return BCUserRole.ADMIN
    if await _check_bc_permission(user, BC_APPROVER_PERMISSION, company_id):
        return BCUserRole.APPROVER
    if await _check_bc_permission(user, BC_EDITOR_PERMISSION, company_id):
        return BCUserRole.EDITOR
    if await _check_bc_permission(user, BC_VIEWER_PERMISSION, company_id):
        return BCUserRole.VIEWER
    
    return None


async def _resolve_bc_role(current_user: dict, company_id: Any) -> BCUserRole | None:
    """Resolve the role for the active company and remember that company.

    The active company is stored on the user dict so route handlers that call
    ``_get_user_bc_role(current_user)`` evaluate the same membership.
    """
    company_id = _coerce_company_id(company_id)
    if company_id is not None:
        current_user["bc_active_company_id"] = company_id
    return await _get_user_bc_role(current_user, company_id)


async def require_bc_viewer(
    current_user: dict = Depends(get_current_user),
    company_id: int | None = Depends(get_active_company_id),
) -> dict:
    """Require BC viewer role (read-only access)."""
    role = await _resolve_bc_role(current_user, company_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BC viewer access required"
        )
    return current_user


async def require_bc_editor(
    current_user: dict = Depends(get_current_user),
    company_id: int | None = Depends(get_active_company_id),
) -> dict:
    """Require BC editor role (can create and edit plans)."""
    role = await _resolve_bc_role(current_user, company_id)
    if role is None or role == BCUserRole.VIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BC editor access required"
        )
    return current_user


async def require_bc_approver(
    current_user: dict = Depends(get_current_user),
    company_id: int | None = Depends(get_active_company_id),
) -> dict:
    """Require BC approver role (can approve plans)."""
    role = await _resolve_bc_role(current_user, company_id)
    if role is None or role in (BCUserRole.VIEWER, BCUserRole.EDITOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BC approver access required"
        )
    return current_user


async def require_bc_admin(
    current_user: dict = Depends(get_current_user),
    company_id: int | None = Depends(get_active_company_id),
) -> dict:
    """Require BC admin role (full administrative access)."""
    role = await _resolve_bc_role(current_user, company_id)
    if role != BCUserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BC admin access required"
        )
    return current_user


async def get_bc_user_role(
    current_user: dict = Depends(get_current_user),
    company_id: int | None = Depends(get_active_company_id),
) -> BCUserRole:
    """Get the BC role for the current user."""
    role = await _resolve_bc_role(current_user, company_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BC system access required"
        )
    return role


async def require_bc_plan_in_active_company(
    request: Request,
    current_user: dict = Depends(get_current_user),
    company_id: int | None = Depends(get_active_company_id),
) -> None:
    """Hide BC plans that belong to another company.

    Applied to every BC plan router. Routes with a ``plan_id`` path
    parameter only resolve when the plan's ``org_id`` is the caller's active
    company; super admins can reach every plan. A missing plan is left for
    the route to report.
    """
    raw_plan_id = request.path_params.get("plan_id")
    if raw_plan_id is None or current_user.get("is_super_admin"):
        return
    from app.repositories import bc3 as bc_repo

    try:
        plan_id = int(raw_plan_id)
    except (TypeError, ValueError):
        return
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        return
    org_id = plan.get("org_id")
    if company_id is None or org_id is None or int(org_id) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")


def resolve_plan_company_id(current_user: dict, company_id: int | None, requested: int | None = None) -> int:
    """Return the company a BC plan list or new plan is scoped to.

    Non-admins are always limited to their active company. Super admins may
    name another company explicitly and otherwise use their active company.
    """
    if current_user.get("is_super_admin") and requested is not None:
        return int(requested)
    if company_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select a company before working with business continuity plans",
        )
    return int(company_id)
