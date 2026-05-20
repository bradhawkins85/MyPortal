"""Chat page routes for the ``chat`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.dependencies.auth import get_current_session
from app.core.config import get_settings
from app.repositories import chat as chat_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import users as user_repo
from app.security.session import SessionData


router = APIRouter(tags=["Chat"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/chat", response_class=HTMLResponse)
async def chat_index(
    request: Request,
    status: str | None = Query(default=None),
    unattended: str | None = Query(default=None),
    session: SessionData | None = Depends(get_current_session),
) -> HTMLResponse:
    settings = get_settings()
    if not settings.matrix_enabled:
        raise HTTPException(status_code=404, detail="Chat is not enabled")
    if not session:
        return RedirectResponse("/login", status_code=303)

    current_user = await user_repo.get_user_by_id(session.user_id)
    if not current_user:
        return RedirectResponse("/login", status_code=303)

    is_super_admin = bool(current_user.get("is_super_admin"))
    is_helpdesk = bool(current_user.get("is_helpdesk_technician"))
    company_id = current_user.get("company_id")
    if not is_super_admin and not is_helpdesk:
        membership = None
        if company_id is not None:
            membership = await user_company_repo.get_user_company(current_user["id"], int(company_id))
        can_access = bool(membership and membership.get("can_access_chat"))
        if not can_access:
            return RedirectResponse("/", status_code=303)

    user_id = current_user["id"]
    is_staff = is_super_admin or is_helpdesk
    unattended_only = is_staff and unattended == "1"

    if is_staff:
        rooms = await chat_repo.list_rooms(status=status, unattended_only=unattended_only)
    else:
        rooms = await chat_repo.list_rooms(user_id=user_id, company_id=company_id, status=status)

    main_module = _main()
    extra = {
        "title": "Chat",
        "rooms": rooms,
        "status_filter": status,
        "unattended_filter": unattended,
        "is_staff": is_staff,
    }
    return await main_module._render_template("chat/index.html", request, current_user, extra=extra)


@router.get("/chat/{room_id}", response_class=HTMLResponse)
async def chat_room_page(
    request: Request,
    room_id: int,
    session: SessionData | None = Depends(get_current_session),
) -> HTMLResponse:
    settings = get_settings()
    if not settings.matrix_enabled:
        raise HTTPException(status_code=404, detail="Chat is not enabled")
    if not session:
        return RedirectResponse("/login", status_code=303)

    current_user = await user_repo.get_user_by_id(session.user_id)
    if not current_user:
        return RedirectResponse("/login", status_code=303)

    room = await chat_repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Chat room not found")

    messages = await chat_repo.get_messages(room_id, limit=50)
    participants = await chat_repo.get_participants(room_id)

    user_id = current_user["id"]
    is_staff = current_user.get("is_super_admin") or current_user.get("is_helpdesk_technician")
    is_creator = room["created_by_user_id"] == user_id

    # Resolve assigned tech display name for staff view
    assigned_tech_display_name = None
    if is_staff and room.get("assigned_tech_user_id"):
        tech = await user_repo.get_user_by_id(room["assigned_tech_user_id"])
        if tech:
            assigned_tech_display_name = (
                " ".join(filter(None, [tech.get("first_name"), tech.get("last_name")]))
                or tech.get("email")
            )

    creator_display_name = None
    if room.get("created_by_user_id"):
        creator = await user_repo.get_user_by_id(room["created_by_user_id"])
        if creator:
            creator_display_name = (
                " ".join(filter(None, [creator.get("first_name"), creator.get("last_name")]))
                or creator.get("email")
            )

    room_dict = dict(room)
    room_dict["assigned_tech_display_name"] = assigned_tech_display_name
    room_dict["creator_display_name"] = creator_display_name

    main_module = _main()
    extra = {
        "title": f"Chat: {room['subject']}",
        "room": room_dict,
        "messages": messages,
        "participants": participants,
        "is_staff": is_staff,
        "is_creator": is_creator,
        "current_user_id": user_id,
        "matrix_is_self_hosted": settings.matrix_is_self_hosted,
    }
    return await main_module._render_template("chat/room.html", request, current_user, extra=extra)


__all__ = ["router"]
