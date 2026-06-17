"""Chat page routes for the ``chat`` feature pack."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.dependencies.auth import get_current_session
from app.core.config import get_settings
from app.repositories import chat as chat_repo
from app.repositories import companies as companies_repo
from app.repositories import tray as tray_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import users as user_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.security.session import SessionData
from app.services import matrix as matrix_service
from app.services import tray as tray_service
from app.services import matrix_ai_waiting_assistant
from app.core.logging import log_error, log_info


router = APIRouter(tags=["Chat"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/chat", response_class=HTMLResponse)
async def chat_index(
    request: Request,
    show_closed: str | None = Query(default=None),
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
    show_closed_filter = show_closed == "1"
    effective_status = None if show_closed_filter else "open"

    if is_staff:
        rooms = await chat_repo.list_rooms(status=effective_status, unattended_only=unattended_only)
    else:
        rooms = await chat_repo.list_rooms(
            user_id=user_id,
            company_id=company_id,
            status=effective_status,
        )

    main_module = _main()
    extra = {
        "title": "Chat",
        "rooms": rooms,
        "show_closed_filter": show_closed_filter,
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
    if is_staff:
        room_dict["ai_extracted_keywords_list"] = chat_repo.decode_ai_json_field(room.get("ai_extracted_keywords")) or []
        room_dict["ai_matched_articles_list"] = chat_repo.decode_ai_json_field(room.get("ai_matched_articles")) or []
        room_dict["ai_show_match_tags"] = settings.matrixbot_ai_show_match_tags

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


# ---------------------------------------------------------------------------
# Tray chat popup — standalone popup page authenticated by one-time token
# ---------------------------------------------------------------------------

_POPUP_SESSION_COOKIE = "tray_popup"
_POPUP_SESSION_TTL_SECONDS = 7200  # 2-hour popup session


def _make_popup_session(
    *,
    device_id: int,
    room_id: int,
    company_id: int,
    csrf_token: str,
) -> str:
    """Return an encrypted cookie value for the tray popup session."""
    exp = (
        datetime.now(timezone.utc) + timedelta(seconds=_POPUP_SESSION_TTL_SECONDS)
    ).isoformat()
    raw = json.dumps(
        {
            "device_id": device_id,
            "room_id": room_id,
            "company_id": company_id,
            "csrf": csrf_token,
            "exp": exp,
        }
    )
    return encrypt_secret(raw)


@router.get("/tray/chat", response_class=HTMLResponse, include_in_schema=False)
async def tray_chat_popup(
    request: Request,
    response: Response,
    token: str = Query(..., description="One-time chat token issued by POST /api/tray/chat-token"),
    room: Optional[int] = Query(default=None, description="Pre-existing room ID (for tech-initiated chats)"),
) -> HTMLResponse:
    """Serve the tray chat popup.

    Validates the one-time ``token`` issued by the tray device to
    ``POST /api/tray/chat-token``, then:

    1. Marks the token as used (single-use).
    2. Creates a new chat room when none is pre-bound, or loads the
       existing room when the token carries a ``room_id``.
    3. Sets a short-lived ``tray_popup`` session cookie (encrypted,
       no user login required).
    4. Returns a minimal standalone chat HTML page.
    """
    settings = get_settings()
    if not settings.matrix_enabled:
        raise HTTPException(status_code=404, detail="Chat is not enabled")

    # ------------------------------------------------------------------
    # 1. Validate the one-time token.
    # ------------------------------------------------------------------
    token_hash = tray_service.hash_token(token)
    token_record = await tray_repo.get_chat_token_by_hash(token_hash)
    if not token_record:
        raise HTTPException(status_code=401, detail="Invalid or expired chat token")
    if token_record.get("used_at") is not None:
        raise HTTPException(status_code=401, detail="Chat token has already been used")
    # Check expiry.
    expires_at = token_record.get("expires_at")
    if expires_at is not None:
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Chat token has expired")

    device_id: int = int(token_record["device_id"])
    device = await tray_repo.get_device_by_id(device_id)
    if not device or device.get("status") == "revoked":
        raise HTTPException(status_code=403, detail="Device not found or revoked")

    company_id: int = int(device.get("company_id") or 0)
    if not company_id:
        raise HTTPException(status_code=403, detail="Device has no associated company")

    company = await companies_repo.get_company_by_id(company_id)
    if not company or not company.get("tray_chat_enabled"):
        raise HTTPException(status_code=403, detail="Chat is not enabled for this company")

    # Mark the token as used immediately to prevent replay.
    await tray_repo.mark_chat_token_used(int(token_record["id"]))

    # ------------------------------------------------------------------
    # 2. Resolve or create the chat room.
    # ------------------------------------------------------------------
    # Prefer room_id embedded in the token (technician-initiated), then
    # the query-string ``room`` param, then create a new room only for an
    # unbound user-initiated tray action. Explicit room launches may come from
    # technician replies or notification actions; if that room is now closed,
    # do not silently create a new "Chat from <device>" room.
    resolved_room_id: int | None = token_record.get("room_id") or room
    explicit_room_requested = resolved_room_id is not None

    chat_room: dict[str, Any] | None = None
    if resolved_room_id:
        chat_room = await chat_repo.get_room(int(resolved_room_id))
        if not chat_room:
            raise HTTPException(status_code=404, detail="Chat room not found")
        if chat_room.get("status") != "open":
            raise HTTPException(status_code=409, detail="Chat room is closed")

    if not chat_room:
        chat_room = await chat_repo.get_open_room_by_device_id(device_id)

    if not chat_room and explicit_room_requested:
        raise HTTPException(status_code=409, detail="Chat room is closed")

    if not chat_room:
        # User-initiated chat: create a fresh room.
        hostname = (device.get("hostname") or "Tray Device")[:200]
        subject = f"Chat from {hostname}"
        try:
            matrix_resp = await matrix_service.create_room(
                name=subject,
                topic=f"Tray-initiated support chat: {subject}",
            )
            matrix_room_id = matrix_resp.get("room_id", "")
            room_alias = matrix_resp.get("room_alias")
        except Exception as exc:
            log_error("tray_chat_popup: failed to create Matrix room", error=str(exc))
            raise HTTPException(status_code=502, detail="Failed to create chat room")

        chat_room = await chat_repo.create_room(
            subject=subject,
            matrix_room_id=matrix_room_id,
            room_alias=room_alias,
            created_by_user_id=None,
            company_id=company_id,
        )
        # Link room to this device.
        room_id_new = int(chat_room["id"])
        try:
            from app.api.routes.tray import _attach_room_to_device as _attach
            await _attach(room_id_new, device_id)
        except Exception:
            pass

        # Apply auto-assign rules.
        try:
            from app.services.chat_auto_assign import apply_auto_assign
            from app.repositories import companies as companies_repo_inner
            company_obj = await companies_repo_inner.get_company_by_id(company_id)
            company_name = (company_obj or {}).get("name") or ""
            await apply_auto_assign(
                room_id_new,
                company_name=company_name,
                subject=subject,
            )
        except Exception as exc:
            log_error("tray_chat_popup: auto-assign failed", room_id=room_id_new, error=str(exc))

        # A tray chat can be opened before the user types a message.  If no
        # technician has been assigned, start the AI waiting-assistant timer so
        # it can acknowledge the new chat instead of waiting for Matrix /sync
        # message activity.
        try:
            refreshed_room = await chat_repo.get_room(room_id_new)
            if refreshed_room and not refreshed_room.get("assigned_tech_user_id"):
                chat_room = refreshed_room
                await matrix_ai_waiting_assistant.handle_chat_opened(room_id_new)
        except Exception as exc:
            log_error("tray_chat_popup: AI waiting assistant open hook failed", room_id=room_id_new, error=str(exc))

        log_info(
            "Tray chat popup: created room",
            room_id=room_id_new,
            device_uid=device.get("device_uid"),
        )

    final_room_id = int(chat_room["id"])

    # ------------------------------------------------------------------
    # 3. Create the popup session cookie.
    # ------------------------------------------------------------------
    csrf_token = secrets.token_hex(16)
    popup_cookie_val = _make_popup_session(
        device_id=device_id,
        room_id=final_room_id,
        company_id=company_id,
        csrf_token=csrf_token,
    )

    # ------------------------------------------------------------------
    # 4. Load initial messages and render the popup.
    # ------------------------------------------------------------------
    messages = await chat_repo.get_messages(final_room_id, limit=50)

    html_content = _render_popup(
        room=chat_room,
        messages=messages,
        csrf_token=csrf_token,
        room_id=final_room_id,
        device_id=device_id,
        hostname=device.get("hostname") or "Tray Device",
    )

    resp = HTMLResponse(content=html_content)
    # Set the popup session cookie — HTTPOnly, SameSite=Strict, short-lived.
    resp.set_cookie(
        key=_POPUP_SESSION_COOKIE,
        value=popup_cookie_val,
        max_age=_POPUP_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=(settings.environment.lower() == "production"),
    )
    return resp


def _render_popup(
    *,
    room: dict[str, Any],
    messages: list[dict[str, Any]],
    csrf_token: str,
    room_id: int,
    device_id: int,
    hostname: str,
) -> str:
    """Render the standalone popup HTML using the Jinja2 template."""
    from jinja2 import Environment, PackageLoader, select_autoescape
    from app.core.config import get_settings

    # Lazy-load the template from the app's templates directory.
    import os

    template_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "templates",
        "tray",
        "chat_popup.html",
    )
    template_path = os.path.normpath(template_path)
    with open(template_path, encoding="utf-8") as f:
        raw = f.read()

    from jinja2 import Template
    tmpl = Template(raw, autoescape=True)

    def _serial(v: Any) -> Any:
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, dict):
            return {k: _serial(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [_serial(i) for i in v]
        return v

    return tmpl.render(
        room=_serial(dict(room)),
        messages=[_serial(m) for m in messages],
        csrf_token=csrf_token,
        room_id=room_id,
        device_id=device_id,
        hostname=hostname,
    )


__all__ = ["router"]
