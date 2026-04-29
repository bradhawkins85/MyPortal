from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.core.logging import set_request_context
from app.repositories import company_memberships as membership_repo
from app.repositories import tray as tray_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import users as user_repo
from app.security.session import SessionData, session_manager
from app.services import issues as issues_service
from app.services import tray as tray_service


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
    # Bind the user id into the logging context so every log line and audit
    # event emitted while handling this request is automatically tagged with
    # the acting user.
    user_id = user.get("id")
    if isinstance(user_id, int):
        set_request_context(user_id=user_id)
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


async def require_issue_tracker_access(current_user: dict = Depends(get_current_user)):
    if current_user.get("is_super_admin"):
        return current_user
    user_id = current_user.get("id")
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Issue tracker access required",
        ) from None
    has_permission = await membership_repo.user_has_permission(
        user_id_int, issues_service.ISSUE_TRACKER_PERMISSION_KEY
    )
    if not has_permission:
        try:
            assignments = await user_company_repo.list_companies_for_user(user_id_int)
        except Exception:  # pragma: no cover - defensive guard against DB issues
            assignments = []
        has_permission = any(bool(entry.get("can_manage_issues")) for entry in assignments)
    if not has_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Issue tracker access required",
        )
    return current_user


async def get_optional_user(request: Request) -> dict | None:
    session = await session_manager.load_session(request)
    if not session:
        return None
    request.state.session = session
    user = await user_repo.get_user_by_id(session.user_id)
    if not user:
        return None
    user_id = user.get("id")
    if isinstance(user_id, int):
        set_request_context(user_id=user_id)
    request.state.active_company_id = session.active_company_id
    if session.active_company_id is not None:
        try:
            membership = await user_company_repo.get_user_company(user["id"], int(session.active_company_id))
        except Exception:  # pragma: no cover - defensive
            membership = None
        if membership is not None:
            request.state.active_membership = membership
    return user


async def get_current_tray_device(request: Request) -> dict:
    """Authenticate a tray-app request using the bearer ``auth_token``.

    The token is extracted from the ``Authorization: Bearer <token>`` header
    or the ``X-Tray-Token`` header (used by the WS handshake).  The lookup is
    by HMAC-SHA256 of the token, so the raw value is never compared.

    Raises :class:`HTTPException` with 401 when the token is missing or
    unknown, or when the matching device has been revoked.
    """

    token = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = request.headers.get("X-Tray-Token", "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tray device authentication required",
        )
    token_hash = tray_service.hash_token(token)
    device = await tray_repo.get_device_by_auth_hash(token_hash)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tray device authentication failed",
        )
    return device
