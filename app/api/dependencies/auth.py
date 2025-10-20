from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.repositories import company_memberships as membership_repo
from app.repositories import users as user_repo
from app.security.session import SessionData, session_manager


async def get_current_session(request: Request) -> SessionData:
    session = await session_manager.load_session(request)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return session


async def get_current_user(
    session: SessionData = Depends(get_current_session),
) -> dict:
    user = await user_repo.get_user_by_id(session.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def require_super_admin(current_user: dict = Depends(get_current_user)):
    if not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    return current_user


async def require_helpdesk_technician(current_user: dict = Depends(get_current_user)):
    if current_user.get("is_super_admin"):
        return current_user
    user_id = current_user.get("id")
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Helpdesk technician privileges required",
        ) from None
    has_permission = await membership_repo.user_has_permission(user_id_int, "helpdesk.technician")
    if not has_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Helpdesk technician privileges required",
        )
    return current_user
