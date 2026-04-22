from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.dependencies.auth import get_current_user
from app.core.config import get_settings
from app.core.logging import log_error
from app.repositories import chat as chat_repo
from app.schemas.chat import (
    ChatMessageCreate,
    ChatRoomCreate,
    ExternalInviteCreate,
)
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import audit as audit_service
from app.services import matrix as matrix_service
from app.services import matrix_admin
from app.services.sanitization import sanitize_rich_text

router = APIRouter(prefix="/api/chat", tags=["Chat"])

_settings = get_settings()
_INVITE_EXPIRE_HOURS = 72


def _serialize(obj: Any) -> Any:
    """Recursively convert datetime/date values to ISO strings for JSON serialization."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    return obj


def _require_matrix_enabled() -> None:
    if not _settings.matrix_enabled:
        raise HTTPException(status_code=404, detail="Matrix chat is not enabled")


@router.get("/rooms", summary="List chat rooms")
async def list_rooms(
    request: Request,
    status: str | None = None,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    _require_matrix_enabled()
    user_id = current_user["id"]
    company_id = current_user.get("company_id")
    is_admin = current_user.get("is_super_admin") or current_user.get("is_helpdesk_technician")

    if is_admin:
        rooms = await chat_repo.list_rooms(status=status)
    else:
        rooms = await chat_repo.list_rooms(user_id=user_id, company_id=company_id, status=status)

    return JSONResponse(_serialize([dict(r) for r in rooms]))


@router.post("/rooms", summary="Create a chat room")
async def create_room(
    request: Request,
    body: ChatRoomCreate,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    _require_matrix_enabled()
    user_id = current_user["id"]
    company_id = current_user.get("company_id")

    try:
        matrix_resp = await matrix_service.create_room(
            name=body.subject,
            topic=f"Support chat: {body.subject}",
        )
        matrix_room_id = matrix_resp.get("room_id", "")
    except Exception as exc:
        log_error("Failed to create Matrix room", error=str(exc))
        raise HTTPException(status_code=502, detail="Failed to create Matrix room")

    room = await chat_repo.create_room(
        subject=body.subject,
        matrix_room_id=matrix_room_id,
        room_alias=matrix_resp.get("room_alias"),
        created_by_user_id=user_id,
        company_id=company_id or 0,
        linked_ticket_id=body.linked_ticket_id,
    )

    mxid = current_user.get("matrix_user_id") or _settings.matrix_bot_user_id or ""
    await chat_repo.add_participant(room["id"], mxid, role="creator", user_id=user_id)

    await audit_service.log_action(
        action="create",
        entity_type="chat_room",
        entity_id=room["id"],
        user_id=user_id,
        new_value={"subject": body.subject},
    )

    return JSONResponse(_serialize(dict(room)), status_code=201)


@router.get("/rooms/{room_id}", summary="Get chat room details")
async def get_room(
    room_id: int,
    request: Request,
    before_event_id: str | None = None,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    _require_matrix_enabled()
    room = await chat_repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    messages = await chat_repo.get_messages(room_id, limit=limit, before_event_id=before_event_id)
    participants = await chat_repo.get_participants(room_id)

    return JSONResponse(_serialize({
        "room": dict(room),
        "messages": [dict(m) for m in messages],
        "participants": [dict(p) for p in participants],
    }))


@router.post("/rooms/{room_id}/messages", summary="Send a message")
async def send_message(
    room_id: int,
    request: Request,
    body: ChatMessageCreate,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    _require_matrix_enabled()
    room = await chat_repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room["status"] == "closed":
        raise HTTPException(status_code=400, detail="Cannot send message to closed room")

    user_id = current_user["id"]
    display_name = current_user.get("display_name") or current_user.get("email", "User")
    sanitized = sanitize_rich_text(body.body) if body.body else None
    safe_body_html = sanitized.html if sanitized else (body.body or "")
    safe_body_text = sanitized.text_content if sanitized else (body.body or "")

    link = await chat_repo.get_chat_user_link(user_id=user_id)
    access_token = None
    if link and link.get("access_token_encrypted"):
        try:
            access_token = decrypt_secret(link["access_token_encrypted"])
        except Exception:
            access_token = None

    formatted_body = f"<strong>{display_name}</strong>: {safe_body_html}" if not access_token else None
    message_body = f"{display_name}: {safe_body_text}" if not access_token else safe_body_text

    try:
        resp = await matrix_service.send_message(
            room["matrix_room_id"],
            message_body,
            formatted_body=formatted_body,
            access_token=access_token,
        )
        event_id = resp.get("event_id")
    except Exception as exc:
        log_error("Failed to send Matrix message", room_id=room_id, error=str(exc))
        raise HTTPException(status_code=502, detail="Failed to send message")

    msg = await chat_repo.add_message(
        room_id=room_id,
        matrix_event_id=event_id,
        sender_matrix_id=current_user.get("matrix_user_id") or _settings.matrix_bot_user_id or "",
        body=body.body,
        sender_user_id=user_id,
        sent_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    return JSONResponse(_serialize(dict(msg)), status_code=201)


@router.post("/rooms/{room_id}/join", summary="Join a chat room (technician/admin)")
async def join_room(
    room_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    _require_matrix_enabled()
    if not (current_user.get("is_super_admin") or current_user.get("is_helpdesk_technician")):
        raise HTTPException(status_code=403, detail="Only technicians or admins can join rooms")

    room = await chat_repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    user_id = current_user["id"]
    mxid = _settings.matrix_bot_user_id or ""

    try:
        await matrix_service.invite_user(room["matrix_room_id"], mxid)
    except Exception:
        pass

    await chat_repo.add_participant(room_id, mxid, role="technician", user_id=user_id)

    # Auto-assign this tech if the room has no assigned technician yet
    if not room.get("assigned_tech_user_id"):
        await chat_repo.assign_tech(room_id, user_id)

    await audit_service.log_action(
        action="join",
        entity_type="chat_room",
        entity_id=room_id,
        user_id=user_id,
        new_value={"role": "technician"},
    )

    return JSONResponse({"status": "joined"})


@router.post("/rooms/{room_id}/assign", summary="Assign a technician to a chat room")
async def assign_room(
    room_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Assign the calling technician/admin to this room, or force-reassign."""
    _require_matrix_enabled()
    if not (current_user.get("is_super_admin") or current_user.get("is_helpdesk_technician")):
        raise HTTPException(status_code=403, detail="Only technicians or admins can be assigned")

    room = await chat_repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    user_id = current_user["id"]
    is_admin = current_user.get("is_super_admin")

    # Admins may forcibly reassign; technicians can only claim unassigned rooms
    if room.get("assigned_tech_user_id") and not is_admin:
        raise HTTPException(status_code=409, detail="Room is already assigned to another technician")

    await chat_repo.reassign_tech(room_id, user_id)

    mxid = _settings.matrix_bot_user_id or ""
    try:
        await matrix_service.invite_user(room["matrix_room_id"], mxid)
    except Exception as exc:
        log_error("Failed to invite bot user to room during assign", room_id=room_id, mxid=mxid, error=str(exc))
    await chat_repo.add_participant(room_id, mxid, role="technician", user_id=user_id)

    await audit_service.log_action(
        action="assign",
        entity_type="chat_room",
        entity_id=room_id,
        user_id=user_id,
        new_value={"assigned_to": user_id},
    )

    return JSONResponse({"status": "assigned", "assigned_tech_user_id": user_id})


@router.post("/rooms/{room_id}/close", summary="Close a chat room")
async def close_room(
    room_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    _require_matrix_enabled()
    room = await chat_repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    user_id = current_user["id"]
    is_admin = current_user.get("is_super_admin") or current_user.get("is_helpdesk_technician")
    if not is_admin and room["created_by_user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to close this room")

    await chat_repo.update_room(room_id, status="closed", updated_at=datetime.utcnow())

    try:
        await matrix_service.send_message(
            room["matrix_room_id"],
            "This chat has been closed.",
        )
    except Exception:
        pass

    await audit_service.log_action(
        action="close",
        entity_type="chat_room",
        entity_id=room_id,
        user_id=user_id,
    )

    return JSONResponse({"status": "closed"})


@router.post("/rooms/{room_id}/invite-external", summary="Generate external Matrix invite (self-hosted only)")
async def invite_external(
    room_id: int,
    request: Request,
    body: ExternalInviteCreate,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    _require_matrix_enabled()
    if not _settings.matrix_is_self_hosted:
        raise HTTPException(status_code=400, detail="External invites require a self-hosted Matrix server")

    room = await chat_repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    user_id = current_user["id"]
    invite_domain = _settings.matrix_invite_domain or _settings.matrix_server_name or ""
    if not invite_domain:
        raise HTTPException(status_code=500, detail="MATRIX_INVITE_DOMAIN is not configured")

    existing_link = None
    if body.target_email:
        existing_link = await chat_repo.get_chat_user_link(email=body.target_email)

    password: str | None = None
    if existing_link:
        mxid = existing_link["matrix_user_id"]
    else:
        localpart = matrix_service.sanitize_localpart(body.target_display_name)
        suffix = secrets.token_hex(4)
        localpart = f"{localpart}_{suffix}"
        mxid = f"@{localpart}:{invite_domain}"
        password = matrix_admin.generate_password()

        try:
            await matrix_admin.create_or_update_user(
                mxid,
                password=password,
                display_name=body.target_display_name,
            )
        except Exception as exc:
            log_error("Failed to provision Matrix user", mxid=mxid, error=str(exc))
            raise HTTPException(status_code=502, detail="Failed to provision Matrix user")

        # Store provisioned user link — the temporary password is NOT persisted.
        # It is returned once in the API response for the admin to communicate
        # securely to the invitee. The invitee should change it on first login.
        await chat_repo.upsert_chat_user_link(
            matrix_user_id=mxid,
            email=body.target_email,
            is_provisioned=True,
        )

    try:
        await matrix_service.invite_user(room["matrix_room_id"], mxid)
    except Exception as exc:
        log_error("Failed to invite Matrix user to room", mxid=mxid, error=str(exc))

    invite_token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=_INVITE_EXPIRE_HOURS)

    invite = await chat_repo.create_invite(
        room_id=room_id,
        created_by_user_id=user_id,
        invite_token=invite_token,
        delivery_method=body.delivery_method.value,
        target_email=body.target_email,
        target_phone=body.target_phone,
        target_display_name=body.target_display_name,
        expires_at=expires_at,
    )

    await chat_repo.update_invite(invite["id"], provisioned_matrix_user_id=mxid, status="pending")

    await audit_service.log_action(
        action="invite_external",
        entity_type="chat_room",
        entity_id=room_id,
        user_id=user_id,
        new_value={"mxid": mxid, "delivery_method": body.delivery_method.value},
    )

    homeserver_url = _settings.matrix_homeserver_url or ""
    deep_link = f"https://app.element.io/#/room/{room['matrix_room_id']}"

    return JSONResponse({
        "invite_id": invite["id"],
        "matrix_user_id": mxid,
        "temporary_password": password,
        "homeserver_url": homeserver_url,
        "deep_link": deep_link,
        "invite_token": invite_token,
        "expires_at": expires_at.isoformat(),
    }, status_code=201)


@router.delete("/invites/{invite_token}", summary="Revoke an external invite")
async def revoke_invite(
    invite_token: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    _require_matrix_enabled()
    invite = await chat_repo.get_invite(invite_token=invite_token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    if invite.get("provisioned_matrix_user_id") and _settings.matrix_is_self_hosted:
        try:
            new_password = matrix_admin.generate_password()
            await matrix_admin.reset_user_password(invite["provisioned_matrix_user_id"], new_password)
        except Exception as exc:
            log_error("Failed to rotate Matrix password on revoke", error=str(exc))

    await chat_repo.update_invite(invite["id"], status="revoked")
    return JSONResponse({"status": "revoked"})


@router.post("/test-connection", summary="Test Matrix connection (admin only)")
async def test_connection(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    _require_matrix_enabled()
    if not current_user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Admin only")

    results: dict[str, Any] = {}

    try:
        whoami = await matrix_service.whoami()
        results["bot_identity"] = whoami.get("user_id")
        results["bot_ok"] = True
    except Exception as exc:
        results["bot_ok"] = False
        results["bot_error"] = str(exc)

    if _settings.matrix_is_self_hosted:
        try:
            from app.services.matrix import _admin_headers, _request
            resp = await _request(
                "GET",
                "/_synapse/admin/v1/server_version",
                headers=_admin_headers(),
            )
            results["admin_ok"] = True
            results["server_version"] = resp.get("server_version")
        except Exception as exc:
            results["admin_ok"] = False
            results["admin_error"] = str(exc)

    return JSONResponse(results)
