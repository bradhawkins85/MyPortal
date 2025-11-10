"""
Role-Based Access Control dependencies for Business Continuity (BC) system.

Provides authentication and authorization dependencies for BC5 API endpoints.
Implements viewer, editor, approver, and admin roles.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.api.dependencies.auth import get_current_user
from app.repositories import company_memberships as membership_repo
from app.schemas.bc5_models import BCUserRole


# Permission keys for BC system
BC_VIEWER_PERMISSION = "bc.viewer"
BC_EDITOR_PERMISSION = "bc.editor"
BC_APPROVER_PERMISSION = "bc.approver"
BC_ADMIN_PERMISSION = "bc.admin"


async def _check_bc_permission(user: dict, permission_key: str) -> bool:
    """Check if user has a specific BC permission."""
    if user.get("is_super_admin"):
        return True
    
    user_id = user.get("id")
    if not user_id:
        return False
    
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return False
    
    try:
        return await membership_repo.user_has_permission(user_id_int, permission_key)
    except Exception:
        return False


async def _get_user_bc_role(user: dict) -> BCUserRole | None:
    """Determine the highest BC role for a user."""
    if user.get("is_super_admin"):
        return BCUserRole.ADMIN
    
    # Check roles in descending order of privilege
    if await _check_bc_permission(user, BC_ADMIN_PERMISSION):
        return BCUserRole.ADMIN
    if await _check_bc_permission(user, BC_APPROVER_PERMISSION):
        return BCUserRole.APPROVER
    if await _check_bc_permission(user, BC_EDITOR_PERMISSION):
        return BCUserRole.EDITOR
    if await _check_bc_permission(user, BC_VIEWER_PERMISSION):
        return BCUserRole.VIEWER
    
    return None


async def require_bc_viewer(current_user: dict = Depends(get_current_user)) -> dict:
    """Require BC viewer role (read-only access)."""
    role = await _get_user_bc_role(current_user)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BC viewer access required"
        )
    return current_user


async def require_bc_editor(current_user: dict = Depends(get_current_user)) -> dict:
    """Require BC editor role (can create and edit plans)."""
    role = await _get_user_bc_role(current_user)
    if role is None or role == BCUserRole.VIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BC editor access required"
        )
    return current_user


async def require_bc_approver(current_user: dict = Depends(get_current_user)) -> dict:
    """Require BC approver role (can approve plans)."""
    role = await _get_user_bc_role(current_user)
    if role is None or role in (BCUserRole.VIEWER, BCUserRole.EDITOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BC approver access required"
        )
    return current_user


async def require_bc_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Require BC admin role (full administrative access)."""
    role = await _get_user_bc_role(current_user)
    if role != BCUserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BC admin access required"
        )
    return current_user


async def get_bc_user_role(current_user: dict = Depends(get_current_user)) -> BCUserRole:
    """Get the BC role for the current user."""
    role = await _get_user_bc_role(current_user)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BC system access required"
        )
    return role
