"""HTTP routes for the MyPortal Tray App.

Routes are split into two groups:

* Device-facing endpoints (``/api/tray/enrol``, ``/api/tray/config``,
  ``/api/tray/heartbeat``) authenticated either with a one-shot install
  token (enrolment) or the long-lived per-device auth token.
* Admin / technician endpoints (``/api/tray/admin/...``) authenticated as
  a normal portal user.

The websocket handler ``/ws/tray/{device_uid}`` is registered in
``app/main.py`` because that module owns all websocket routing.
"""

from __future__ import annotations

import html as _html
import json
import secrets
import zlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.api.dependencies.auth import (
    get_current_tray_device,
    get_current_user,
    require_super_admin,
)
from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.repositories import assets as assets_repo
from app.repositories import chat as chat_repo
from app.repositories import companies as companies_repo
from app.repositories import tray as tray_repo
from app.repositories import tickets as tickets_repo
from app.repositories import users as users_repo
from app.schemas.tray import (
    TrayChatStartRequest,
    TrayChatStartResponse,
    TrayChatTokenResponse,
    TrayConfigResponse,
    TrayEnrolRequest,
    TrayEnrolResponse,
    TrayHeartbeatRequest,
    TrayInstallTokenCreate,
    TrayInstallTokenResponse,
    TrayMenuConfigCreate,
    TrayMenuConfigUpdate,
    TrayTicketQuestionCreate,
    TrayTicketQuestionsResponse,
    TrayTicketQuestionUpdate,
    TrayTicketSubmitRequest,
    TrayTicketSubmitResponse,
    TrayTRMMScriptRunRequest,
    TrayTRMMScriptRunResponse,
)
from app.services import audit as audit_service
from app.services import chat_ticket_sync
from app.services import chat_ntfy_notifications
from app.services import matrix as matrix_service
from app.services import matrix_ai_waiting_assistant
from app.services import tacticalrmm as tacticalrmm_service
from app.services import tickets as tickets_service
from app.services import tray as tray_service
from app.services import tray_ticket_questions as tq_service
from app.services.sanitization import sanitize_rich_text
from app.security.encryption import decrypt_secret, encrypt_secret
from app.repositories import tray_ticket_questions as tq_repo

router = APIRouter(prefix="/api/tray", tags=["Tray App"])

_settings = get_settings()


def _serialise_popup_chat_value(obj: Any) -> Any:
    """Convert popup chat payload values into JSON-compatible objects."""

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        if obj == obj.to_integral():
            return int(obj)
        return float(obj)
    if isinstance(obj, dict):
        return {key: _serialise_popup_chat_value(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_serialise_popup_chat_value(item) for item in obj]
    return obj

# ---------------------------------------------------------------------------
# Device-facing endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/enrol",
    response_model=TrayEnrolResponse,
    summary="Enrol a tray device using an install token",
)
async def enrol_device(
    payload: TrayEnrolRequest, request: Request
) -> TrayEnrolResponse:
    """Validate an install token, create or update a ``tray_devices`` row,
    and return the long-lived auth token the client must use thereafter."""

    install_token = (payload.install_token or "").strip()
    if not install_token:
        raise HTTPException(status_code=400, detail="install_token is required")

    token_record = await tray_repo.get_install_token_by_hash(
        tray_service.hash_token(install_token)
    )
    if not token_record:
        raise HTTPException(status_code=401, detail="Invalid install token")
    if token_record.get("revoked_at"):
        raise HTTPException(status_code=401, detail="Install token has been revoked")
    expires_at = token_record.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.replace(tzinfo=expires_at.tzinfo or timezone.utc) < datetime.now(
            timezone.utc
        ):
            raise HTTPException(status_code=401, detail="Install token has expired")

    company_id = token_record.get("company_id")
    device_uid = tray_service.normalise_device_uid(payload.device_uid)
    auth_token = tray_service.generate_auth_token()
    auth_hash = tray_service.hash_token(auth_token)
    auth_prefix = tray_service.token_prefix(auth_token)

    # Try to match to an existing asset by serial / hostname.
    asset_id = await tray_service.find_matching_asset(
        company_id=company_id,
        serial_number=payload.serial_number,
        hostname=payload.hostname,
    )

    existing = await tray_repo.get_device_by_uid(device_uid)
    if existing:
        await tray_repo.update_device_auth(
            int(existing["id"]),
            auth_token_hash=auth_hash,
            auth_token_prefix=auth_prefix,
        )
        if asset_id and not existing.get("asset_id"):
            await tray_repo.link_device_to_asset(int(existing["id"]), asset_id)
        device = await tray_repo.get_device_by_uid(device_uid) or existing
    else:
        device = await tray_repo.create_device(
            company_id=company_id,
            asset_id=asset_id,
            device_uid=device_uid,
            enrolment_token_id=int(token_record["id"]),
            auth_token_hash=auth_hash,
            auth_token_prefix=auth_prefix,
            os=payload.os,
            os_version=payload.os_version,
            hostname=payload.hostname,
            serial_number=payload.serial_number,
            agent_version=payload.agent_version,
            console_user=payload.console_user,
            status="active",
        )

    await tray_repo.mark_install_token_used(int(token_record["id"]))
    log_info(
        "Tray device enrolled",
        device_uid=device_uid,
        company_id=company_id,
        asset_id=asset_id,
    )

    return TrayEnrolResponse(
        device_uid=device_uid,
        auth_token=auth_token,
        company_id=device.get("company_id"),
        asset_id=device.get("asset_id"),
    )


@router.get(
    "/config",
    response_model=TrayConfigResponse,
    summary="Fetch the resolved tray menu configuration for this device",
)
async def get_device_config(
    device: dict = Depends(get_current_tray_device),
) -> TrayConfigResponse:
    config = await tray_service.resolve_config_for_device(device)
    chat_enabled = False
    if device.get("company_id"):
        company = await companies_repo.get_company_by_id(int(device["company_id"]))
        chat_enabled = bool(
            company and company.get("tray_chat_enabled") and _settings.matrix_enabled
        )
    return TrayConfigResponse(
        version=int(config.get("version") or 1),
        menu=config.get("menu") or [],
        display_text=config.get("display_text"),
        branding_icon_url=config.get("branding_icon_url"),
        branding_display_name=config.get("branding_display_name"),
        env_allowlist=config.get("env_allowlist") or [],
        chat_enabled=chat_enabled,
        chat_client_mode=config.get("chat_client_mode") or None,
    )


def _find_trmm_script_node(
    nodes: list[dict[str, Any]], script_id: int
) -> dict[str, Any] | None:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "").strip().lower()
        try:
            node_script_id = int(node.get("script_id") or 0)
        except (TypeError, ValueError):
            node_script_id = 0
        if node_type == "trmm_script" and node_script_id == int(script_id):
            return node
        children = node.get("children")
        if isinstance(children, list):
            found = _find_trmm_script_node(children, script_id)
            if found:
                return found
    return None


@router.post(
    "/trmm-script",
    response_model=TrayTRMMScriptRunResponse,
    summary="Run a configured Tactical RMM script for this tray device",
)
async def run_trmm_script(
    payload: TrayTRMMScriptRunRequest,
    device: dict = Depends(get_current_tray_device),
) -> TrayTRMMScriptRunResponse:
    """Run a menu-approved Tactical RMM script against the linked asset.

    The script ID must exist in this device's resolved tray menu. This prevents
    a compromised tray client from asking MyPortal to execute arbitrary scripts
    that an administrator did not expose in the menu designer.
    """

    config = await tray_service.resolve_config_for_device(device)
    script_node = _find_trmm_script_node(
        config.get("menu") or [], int(payload.script_id)
    )
    if not script_node:
        raise HTTPException(
            status_code=403, detail="TRMM script is not enabled for this tray device"
        )

    asset_id = device.get("asset_id")
    if not asset_id:
        raise HTTPException(
            status_code=409, detail="Tray device is not linked to an asset"
        )
    asset = await assets_repo.get_asset_by_id(int(asset_id))
    if not asset:
        raise HTTPException(status_code=409, detail="Linked asset was not found")
    trmm_agent_id = str(asset.get("tactical_asset_id") or "").strip()
    if not trmm_agent_id:
        raise HTTPException(
            status_code=409, detail="Linked asset does not have a Tactical RMM agent ID"
        )

    try:
        result = await tacticalrmm_service.run_script_on_agent(
            trmm_agent_id, int(payload.script_id)
        )
    except tacticalrmm_service.TacticalRMMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except tacticalrmm_service.TacticalRMMAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    response = result.get("response") if isinstance(result, dict) else None
    event_id = None
    if isinstance(response, dict) and response.get("event_id") is not None:
        try:
            event_id = int(response["event_id"])
        except (TypeError, ValueError):
            event_id = None
    log_info(
        "Tray requested Tactical RMM script",
        device_id=device.get("id"),
        asset_id=asset_id,
        trmm_agent_id=trmm_agent_id,
        script_id=payload.script_id,
        event_id=event_id,
    )
    return TrayTRMMScriptRunResponse(
        status="queued",
        script_id=int(payload.script_id),
        script_name=str(
            script_node.get("script_name") or script_node.get("label") or ""
        )
        or None,
        event_id=event_id,
        message="The requested automation has been scheduled and will run in the background shortly.",
    )


@router.post(
    "/heartbeat",
    summary="Lightweight liveness ping; updates last_seen / console user / IP",
)
async def heartbeat(
    payload: TrayHeartbeatRequest,
    request: Request,
    device: dict = Depends(get_current_tray_device),
) -> JSONResponse:
    client_ip = payload.last_ip or (
        request.client.host if request.client else None
    )
    await tray_repo.update_device_heartbeat(
        int(device["id"]),
        console_user=payload.console_user,
        last_ip=client_ip,
        agent_version=payload.agent_version,
    )
    return JSONResponse({"status": "ok"})


@router.get(
    "/ticket-questions",
    response_model=TrayTicketQuestionsResponse,
    summary="Fetch dynamic intake questions for the Submit Ticket dialog",
)
async def get_ticket_questions(
    device: dict = Depends(get_current_tray_device),
) -> TrayTicketQuestionsResponse:
    """Return the ordered list of dynamic intake questions for this device.

    Global questions are returned first, followed by company-scoped questions.
    Each question includes its conditional visibility rules so the client can
    show or hide follow-up questions based on earlier answers.
    """
    company_id: int | None = device.get("company_id")
    questions = await tq_service.get_questions_for_company(company_id)
    return TrayTicketQuestionsResponse(questions=questions)


@router.post(
    "/submit-ticket",
    response_model=TrayTicketSubmitResponse,
    summary="Submit a support ticket from a tray device",
)
async def tray_submit_ticket(
    payload: TrayTicketSubmitRequest,
    request: Request,
) -> TrayTicketSubmitResponse:
    """Create a support ticket submitted via the tray icon.

    The bearer auth token is the preferred device identity and is used to link
    the ticket to the corresponding asset and company.  ``device_uid`` remains
    accepted as a backwards-compatible fallback for older tray clients that do
    not send bearer auth.  Name, email, and phone are provided by the user in
    the tray dialog; email is used to match an existing portal user account
    when one exists.

    Dynamic question answers are validated server-side against the current
    question definitions.  Required visible questions must have a non-empty
    value; select questions must use a declared option.
    """
    # Prefer the bearer auth token when available. Older tray clients also send
    # device_uid in the JSON body, but relying exclusively on that body value
    # makes submissions fragile when the UI has an auth token yet cannot read
    # the service-written DeviceUID from registry/state. The token already
    # identifies the enrolled device, so use it as the authoritative source and
    # keep device_uid as a backwards-compatible fallback for existing clients.
    device = None
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if token:
        device = await tray_repo.get_device_by_auth_hash(tray_service.hash_token(token))
        if not device:
            raise HTTPException(
                status_code=401, detail="Tray device authentication failed"
            )

    if device is None and payload.device_uid:
        device = await tray_repo.get_device_by_uid(payload.device_uid)

    if not device or device.get("status") == "revoked":
        raise HTTPException(status_code=404, detail="Device not found")

    company_id: int | None = device.get("company_id")
    asset_id: int | None = device.get("asset_id")

    # Normalise email once; reused for both lookup and ticket creation.
    normalised_email = payload.email.strip().lower()

    # Attempt to resolve an existing portal user by email so the ticket
    # appears under their account and they receive notifications normally.
    requester_id: int | None = None
    existing_user = await users_repo.get_user_by_email(normalised_email)
    if existing_user:
        requester_id = int(existing_user["id"])

    # ---------------------------------------------------------------------------
    # Validate dynamic answers (if any questions are configured)
    # ---------------------------------------------------------------------------
    submitted_answers = [
        {"question_id": a.question_id, "value": a.value}
        for a in (payload.answers or [])
    ]
    questions = await tq_service.get_questions_for_company(company_id)
    if questions:
        errors = tq_service.validate_answers(questions, submitted_answers)
        if errors:
            raise HTTPException(status_code=422, detail="; ".join(errors))

    # ---------------------------------------------------------------------------
    # Build description
    # ---------------------------------------------------------------------------
    # Contact block: include when no portal account is matched.
    # Values are HTML-escaped before embedding to prevent markdown injection.
    description_parts: list[str] = []
    if requester_id is None:
        safe_name = _html.escape(payload.name)
        safe_email = _html.escape(normalised_email)
        contact_line = f"**Name:** {safe_name}"
        if payload.phone:
            safe_phone = _html.escape(payload.phone)
            contact_line += f"  |  **Phone:** {safe_phone}"
        contact_line += f"  |  **Email:** {safe_email}"
        description_parts.append(contact_line)
        description_parts.append("")  # blank line before user content

    if payload.description:
        description_parts.append(payload.description)

    # Append additional details block from dynamic answers.
    if questions and submitted_answers:
        additional = tq_service.build_additional_details(questions, submitted_answers)
        if additional:
            if description_parts:
                description_parts.append("")
            description_parts.append(additional)

    full_description = "\n".join(description_parts) if description_parts else None

    sanitized = sanitize_rich_text(full_description) if full_description else None
    description_html = sanitized.html if sanitized else None

    status_value = await tickets_service.resolve_status_or_default(None)
    ticket = await tickets_service.create_ticket(
        subject=payload.subject.strip(),
        description=description_html,
        requester_id=requester_id,
        company_id=company_id,
        assigned_user_id=None,
        priority="normal",
        status=status_value,
        category=None,
        module_slug=None,
        external_reference=None,
        trigger_automations=True,
        initial_reply_author_id=requester_id,
        requester_email=normalised_email if requester_id is None else None,
    )

    # Link to the device's asset when one is known.
    if asset_id:
        try:
            await tickets_repo.replace_ticket_assets(int(ticket["id"]), [int(asset_id)])
        except Exception as exc:  # pragma: no cover - defensive
            log_info(
                "tray_submit_ticket: could not link asset",
                ticket_id=ticket.get("id"),
                asset_id=asset_id,
                error=str(exc),
            )

    # Persist answer snapshots for audit.
    if questions and submitted_answers:
        try:
            snapshots = tq_service.build_answer_snapshots(questions, submitted_answers)
            if snapshots:
                await tq_repo.create_answers(int(ticket["id"]), snapshots)
        except Exception as exc:  # pragma: no cover - defensive
            log_info(
                "tray_submit_ticket: could not persist answer snapshots",
                ticket_id=ticket.get("id"),
                error=str(exc),
            )

    log_info(
        "Tray ticket submitted",
        device_uid=device.get("uid") or payload.device_uid,
        ticket_id=ticket.get("id"),
        requester_id=requester_id,
    )

    return TrayTicketSubmitResponse(
        ticket_id=int(ticket["id"]),
        ticket_number=ticket.get("ticket_number"),
    )


# ---------------------------------------------------------------------------
# Admin endpoints (install tokens + menu configs)
# ---------------------------------------------------------------------------


@router.get(
    "/admin/install-tokens",
    summary="List install tokens (admin)",
)
async def list_install_tokens(
    company_id: int | None = None,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    rows = await tray_repo.list_install_tokens(company_id=company_id)
    return JSONResponse([_serialise_token(row) for row in rows])


@router.post(
    "/admin/install-tokens",
    response_model=TrayInstallTokenResponse,
    summary="Generate a new install token (admin)",
)
async def create_install_token(
    payload: TrayInstallTokenCreate,
    current_user: dict = Depends(require_super_admin),
) -> TrayInstallTokenResponse:
    raw_token = tray_service.generate_install_token()
    record = await tray_repo.create_install_token(
        label=payload.label,
        company_id=payload.company_id,
        token_hash=tray_service.hash_token(raw_token),
        token_prefix=tray_service.token_prefix(raw_token),
        created_by_user_id=int(current_user["id"]),
        expires_at=payload.expires_at,
    )
    response = _serialise_token(record)
    response["token"] = raw_token
    return TrayInstallTokenResponse(**response)


@router.post(
    "/admin/install-tokens/{token_id}/revoke",
    summary="Revoke an install token (admin)",
)
async def revoke_install_token(
    token_id: int,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    await tray_repo.revoke_install_token(token_id)
    return JSONResponse({"status": "revoked"})


@router.get(
    "/admin/configs",
    summary="List tray menu configurations",
)
async def list_menu_configs(
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    rows = await tray_repo.list_menu_configs()
    return JSONResponse([_serialise_config(row) for row in rows])


@router.post(
    "/admin/configs",
    summary="Create a tray menu configuration",
)
async def create_menu_config(
    payload: TrayMenuConfigCreate,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    safe_text = _sanitise_display_text(payload.display_text)
    record = await tray_repo.create_menu_config(
        name=payload.name,
        scope=payload.scope,
        scope_ref_id=payload.scope_ref_id,
        payload_json=json.dumps([n.model_dump(exclude_none=True) for n in payload.payload]),
        display_text=safe_text,
        env_allowlist=",".join(payload.env_allowlist),
        branding_icon_url=payload.branding_icon_url,
        enabled=payload.enabled,
        created_by_user_id=int(current_user["id"]),
    )
    return JSONResponse(_serialise_config(record), status_code=201)


@router.put(
    "/admin/configs/{config_id}",
    summary="Update a tray menu configuration",
)
async def update_menu_config(
    config_id: int,
    payload: TrayMenuConfigUpdate,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    existing = await tray_repo.get_menu_config(config_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Configuration not found")
    payload_json = None
    if payload.payload is not None:
        payload_json = json.dumps([n.model_dump(exclude_none=True) for n in payload.payload])
    safe_text = (
        _sanitise_display_text(payload.display_text)
        if payload.display_text is not None
        else None
    )
    env_csv = (
        ",".join(payload.env_allowlist) if payload.env_allowlist is not None else None
    )
    await tray_repo.update_menu_config(
        config_id,
        name=payload.name,
        payload_json=payload_json,
        display_text=safe_text,
        env_allowlist=env_csv,
        branding_icon_url=payload.branding_icon_url,
        enabled=payload.enabled,
        updated_by_user_id=int(current_user["id"]),
    )
    refreshed = await tray_repo.get_menu_config(config_id)
    return JSONResponse(_serialise_config(refreshed or existing))


@router.delete(
    "/admin/configs/{config_id}",
    summary="Delete a tray menu configuration",
)
async def delete_menu_config(
    config_id: int,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    await tray_repo.delete_menu_config(config_id)
    return JSONResponse({"status": "deleted"})


@router.get(
    "/admin/devices",
    summary="List enrolled tray devices",
)
async def list_devices(
    company_id: int | None = None,
    status_filter: str | None = None,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    if not (current_user.get("is_super_admin") or current_user.get("is_helpdesk_technician")):
        raise HTTPException(status_code=403, detail="Helpdesk technician privileges required")
    rows = await tray_repo.list_devices(company_id=company_id, status=status_filter)
    return JSONResponse([_serialise_device(row) for row in rows])


@router.post(
    "/admin/devices/{device_id}/revoke",
    summary="Revoke a tray device (forces re-enrolment)",
)
async def admin_revoke_device(
    device_id: int,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    await tray_repo.revoke_device(device_id)
    return JSONResponse({"status": "revoked"})


@router.post(
    "/admin/devices/{device_id}/reactivate",
    summary="Reactivate a revoked tray device",
)
async def admin_reactivate_device(
    device_id: int,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    device = await tray_repo.get_device_by_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Tray device not found")
    if device.get("status") != "revoked":
        raise HTTPException(
            status_code=400,
            detail="Cannot reactivate device: device is not in revoked state",
        )
    await tray_repo.reactivate_device(device_id)
    return JSONResponse({"status": "active"})


@router.delete(
    "/admin/devices/{device_id}",
    summary="Delete a revoked tray device",
)
async def admin_delete_device(
    device_id: int,
    _current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    device = await tray_repo.get_device_by_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Tray device not found")
    if device.get("status") != "revoked":
        raise HTTPException(
            status_code=400,
            detail="Cannot delete device: device is not in revoked state",
        )
    await tray_repo.delete_device(device_id)
    return JSONResponse({"status": "deleted"})


@router.post(
    "/admin/devices/bulk-delete-revoked",
    summary="Delete all revoked tray devices",
)
async def admin_bulk_delete_revoked_devices(
    _current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    deleted_count = await tray_repo.delete_revoked_devices()
    return JSONResponse({"status": "ok", "deleted_count": deleted_count})


# ---------------------------------------------------------------------------
# Technician chat-start
# ---------------------------------------------------------------------------


@router.post(
    "/{device_uid}/chat/start",
    response_model=TrayChatStartResponse,
    summary="Initiate a chat with a tray device's active console session",
)
async def start_device_chat(
    device_uid: str,
    payload: TrayChatStartRequest,
    current_user: dict = Depends(get_current_user),
) -> TrayChatStartResponse:
    if not _settings.matrix_enabled:
        raise HTTPException(status_code=404, detail="Matrix chat is not enabled")
    device = await tray_repo.get_device_by_uid(device_uid)
    if not device or device.get("status") != "active":
        raise HTTPException(status_code=404, detail="Tray device not found or not active")

    company = None
    if device.get("company_id"):
        company = await companies_repo.get_company_by_id(int(device["company_id"]))
    if not tray_service.technician_can_initiate(current_user, company):
        raise HTTPException(
            status_code=403,
            detail="Technician chat with assets is disabled for this company",
        )

    subject = (payload.subject or "Tray support chat").strip()[:500]

    # Reconnect technicians to the device's current open chat first. User-
    # initiated tray chats are linked to the device, so this prevents creating
    # duplicate Matrix rooms while an active support conversation already
    # exists.
    room = await chat_repo.get_open_room_by_device_id(int(device["id"]))
    matrix_room_id = str((room or {}).get("matrix_room_id") or "")

    created_room = False
    if not room:
        # Reuse Matrix room creation + chat_rooms persistence so the message
        # appears in the standard /chat experience.
        try:
            matrix_resp = await matrix_service.create_room(
                name=subject,
                topic=f"Tray-initiated support chat: {subject}",
            )
            matrix_room_id = matrix_resp.get("room_id", "")
        except Exception as exc:
            log_error("Failed to create Matrix room for tray chat", error=str(exc))
            raise HTTPException(status_code=502, detail="Failed to create chat room") from exc

        room = await chat_repo.create_room(
            subject=subject,
            matrix_room_id=matrix_room_id,
            room_alias=matrix_resp.get("room_alias"),
            created_by_user_id=int(current_user["id"]),
            company_id=int(device.get("company_id") or 0),
            linked_ticket_id=None,
        )

        # Link the room back to its device so the UI can highlight it.
        await _attach_room_to_device(int(room["id"]), int(device["id"]))
        created_room = True

    if created_room and not room.get("assigned_tech_user_id"):
        room_id = int(room["id"])
        user_id = int(current_user["id"])
        await chat_repo.reassign_tech(room_id, user_id)
        await matrix_ai_waiting_assistant.handle_technician_takeover(room_id, user_id)

        tech_mxid = current_user.get("matrix_user_id") or ""
        bot_mxid = _settings.matrix_bot_user_id or ""
        if tech_mxid:
            try:
                await matrix_service.invite_user(matrix_room_id, tech_mxid)
            except Exception as exc:
                log_error(
                    "Failed to invite technician to tray chat during auto-assign",
                    room_id=room_id,
                    mxid=tech_mxid,
                    error=str(exc),
                )
            try:
                await matrix_service.set_user_power_level(matrix_room_id, tech_mxid, 100)
            except Exception as exc:
                log_error(
                    "Failed to set Matrix power level during tray chat auto-assign",
                    room_id=room_id,
                    mxid=tech_mxid,
                    error=str(exc),
                )
            await chat_repo.add_participant(
                room_id, tech_mxid, role="technician", user_id=user_id
            )
        elif bot_mxid:
            try:
                await matrix_service.invite_user(matrix_room_id, bot_mxid)
            except Exception as exc:
                log_error(
                    "Failed to invite bot user to tray chat during auto-assign",
                    room_id=room_id,
                    mxid=bot_mxid,
                    error=str(exc),
                )
            await chat_repo.add_participant(
                room_id, bot_mxid, role="technician", user_id=user_id
            )

    initial_message = (payload.message or "").strip()[:4000]
    initiator_name = current_user.get("display_name") or current_user.get("email")
    if initial_message:
        matrix_event_id: str | None = None
        try:
            matrix_event = await matrix_service.send_message(
                room_id=matrix_room_id,
                body=initial_message,
                sender_display_name=initiator_name,
            )
            matrix_event_id = matrix_event.get("event_id")
        except Exception as exc:
            log_error(
                "Failed to send initial tray chat message",
                room_id=room["id"],
                error=str(exc),
            )

        sent_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await chat_repo.add_message(
            room_id=int(room["id"]),
            matrix_event_id=matrix_event_id,
            sender_matrix_id=(
                current_user.get("matrix_user_id") or _settings.matrix_bot_user_id or ""
            ),
            body=initial_message,
            sender_user_id=int(current_user["id"]),
            sender_display_name=initiator_name,
            sent_at=sent_at,
        )
        await chat_repo.update_room(
            int(room["id"]),
            last_message_at=sent_at,
            updated_at=sent_at,
        )

    delivered = await tray_service.send_to_device(
        device_uid,
        {
            "type": "chat_open",
            "room_id": int(room["id"]),
            "matrix_room_id": matrix_room_id,
            "subject": subject,
            "initiated_by": initiator_name,
            "message": initial_message,
        },
    )
    await tray_repo.log_command(
        device_id=int(device["id"]),
        command="chat_open",
        payload_json=json.dumps({"room_id": int(room["id"])}),
        initiated_by_user_id=int(current_user["id"]),
        status="delivered" if delivered else "queued",
    )

    await audit_service.log_action(
        action="tray_chat_start",
        entity_type="tray_device",
        entity_id=int(device["id"]),
        user_id=int(current_user["id"]),
        new_value={"room_id": int(room["id"]), "delivered": delivered},
    )

    return TrayChatStartResponse(
        room_id=int(room["id"]),
        matrix_room_id=matrix_room_id,
        delivered=delivered,
    )


# ---------------------------------------------------------------------------
# Admin — ticket question management
# ---------------------------------------------------------------------------


def _serialise_question(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "scope": row.get("scope"),
        "company_id": row.get("company_id"),
        "field_type": row.get("field_type"),
        "label": row.get("label"),
        "placeholder": row.get("placeholder"),
        "is_required": bool(row.get("is_required")),
        "options": row.get("options") or [],
        "sort_order": int(row.get("sort_order") or 0),
        "is_active": bool(row.get("is_active")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get(
    "/admin/ticket-questions",
    summary="List ticket intake question definitions (admin)",
)
async def admin_list_ticket_questions(
    scope: str | None = None,
    company_id: int | None = None,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    rows = await tq_repo.list_questions(scope=scope, company_id=company_id)
    all_ids = [int(r["id"]) for r in rows]
    cond_rows = await tq_repo.list_conditions_for_questions(all_ids)
    cond_index: dict[int, list[dict[str, Any]]] = {}
    for cond in cond_rows:
        cond_index.setdefault(int(cond["question_id"]), []).append(dict(cond))
    out = []
    for r in rows:
        q = _serialise_question(r)
        q["conditions"] = cond_index.get(int(r["id"]), [])
        out.append(q)
    return JSONResponse(out)


@router.post(
    "/admin/ticket-questions",
    summary="Create a ticket intake question definition (admin)",
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_ticket_question(
    payload: TrayTicketQuestionCreate,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    if payload.scope == "company" and payload.company_id is None:
        raise HTTPException(
            status_code=422, detail="company_id is required for company-scoped questions"
        )
    record = await tq_repo.create_question(
        scope=payload.scope,
        company_id=payload.company_id,
        field_type=payload.field_type,
        label=payload.label,
        placeholder=payload.placeholder,
        is_required=payload.is_required,
        options=payload.options,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
        created_by_user_id=int(current_user["id"]),
    )
    if payload.conditions:
        await tq_repo.replace_conditions_for_question(
            int(record["id"]),
            [c.model_dump() for c in payload.conditions],
        )
    record["conditions"] = [c.model_dump() for c in payload.conditions]
    return JSONResponse(_serialise_question(record) | {"conditions": record["conditions"]})


@router.put(
    "/admin/ticket-questions/{question_id}",
    summary="Update a ticket intake question definition (admin)",
)
async def admin_update_ticket_question(
    question_id: int,
    payload: TrayTicketQuestionUpdate,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    existing = await tq_repo.get_question(question_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Question not found")
    updated = await tq_repo.update_question(
        question_id,
        field_type=payload.field_type,
        label=payload.label,
        placeholder=payload.placeholder,
        is_required=payload.is_required,
        options=payload.options,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    if payload.conditions is not None:
        await tq_repo.replace_conditions_for_question(
            question_id,
            [c.model_dump() for c in payload.conditions],
        )
    conditions = await tq_repo.list_conditions_for_question(question_id)
    out = _serialise_question(updated or existing)
    out["conditions"] = conditions
    return JSONResponse(out)


@router.delete(
    "/admin/ticket-questions/{question_id}",
    summary="Delete a ticket intake question definition (admin)",
)
async def admin_delete_ticket_question(
    question_id: int,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    existing = await tq_repo.get_question(question_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Question not found")
    await tq_repo.delete_question(question_id)
    return JSONResponse({"status": "deleted"})


def _sanitise_display_text(value: str | None) -> str | None:
    if not value:
        return value
    sanitized = sanitize_rich_text(value)
    return sanitized.html if sanitized else None


def _serialise_token(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "label": row.get("label"),
        "company_id": row.get("company_id"),
        "token": None,
        "token_prefix": row.get("token_prefix") or "",
        "created_at": row.get("created_at"),
        "expires_at": row.get("expires_at"),
        "revoked_at": row.get("revoked_at"),
        "last_used_at": row.get("last_used_at"),
        "use_count": int(row.get("use_count") or 0),
    }


def _serialise_config(row: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(row.get("payload_json") or "[]")
    except (ValueError, TypeError):
        payload = []
    return {
        "id": int(row["id"]),
        "name": row.get("name"),
        "scope": row.get("scope"),
        "scope_ref_id": row.get("scope_ref_id"),
        "payload": payload,
        "display_text": row.get("display_text"),
        "env_allowlist": [
            v.strip()
            for v in str(row.get("env_allowlist") or "").split(",")
            if v.strip()
        ],
        "branding_icon_url": row.get("branding_icon_url"),
        "enabled": bool(row.get("enabled")),
        "version": int(row.get("version") or 1),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _serialise_device(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "device_uid": row.get("device_uid"),
        "company_id": row.get("company_id"),
        "asset_id": row.get("asset_id"),
        "hostname": row.get("hostname"),
        "os": row.get("os"),
        "os_version": row.get("os_version"),
        "console_user": row.get("console_user"),
        "last_ip": row.get("last_ip"),
        "last_seen_utc": row.get("last_seen_utc"),
        "status": row.get("status"),
        "agent_version": row.get("agent_version"),
        "auth_token_prefix": row.get("auth_token_prefix"),
    }


async def _attach_room_to_device(room_id: int, device_id: int) -> None:
    """Set the ``tray_device_id`` link on ``chat_rooms``.

    This is implemented here (rather than in chat_repo) because the column
    is owned by the tray feature and is nullable for non-tray rooms.
    """
    from app.core.database import db

    placeholder = "?" if db.is_sqlite() else "%s"
    try:
        await db.execute(
            f"UPDATE chat_rooms SET tray_device_id = {placeholder} "
            f"WHERE id = {placeholder}",
            (device_id, room_id),
        )
    except Exception as exc:  # pragma: no cover - defensive
        log_error("Failed to link chat room to tray device", room_id=room_id, error=str(exc))


# ---------------------------------------------------------------------------
# Phase 5 – Auto-update version endpoint
# ---------------------------------------------------------------------------

# _rollout_bucket returns a deterministic integer in [0, 100) for a given
# device identifier, used to assign a device to a rollout cohort.
def _rollout_bucket(device_uid: str) -> int:
    return zlib.crc32(device_uid.encode("utf-8")) % 100


@router.get(
    "/version",
    summary="Check the latest available tray installer version",
    tags=["Tray App"],
)
async def get_tray_version(request: Request) -> JSONResponse:
    """Device-facing endpoint; polled on a jittered ~6-hour timer.

    Returns the version the calling device should install.  When the
    newest published version has ``rollout_percent < 100`` (a staged
    rollout) the server uses the device's auth token to look up its
    ``device_uid`` and places it in a deterministic bucket
    ``crc32(device_uid) % 100``.  Devices whose bucket ≥
    ``rollout_percent`` are held back and receive the previous fully-
    rolled-out (``rollout_percent = 100``) version instead, spreading
    downloads across the fleet and avoiding a thundering-herd effect.

    Devices that are not authenticated (no Authorization header) are always
    served the latest version.
    """
    agent_os = request.headers.get("X-Tray-OS", "all").lower()
    row = await tray_repo.get_latest_tray_version(agent_os)
    if not row:
        row = await tray_repo.get_latest_tray_version("all")
    if not row:
        return JSONResponse({"version": "0.0.0", "download_url": None, "required": False})

    rollout_percent = int(row.get("rollout_percent") or 100)

    # When the newest version is not fully rolled out, check whether this
    # device falls within the rollout cohort.
    if rollout_percent < 100:
        device_uid: str | None = None

        # Attempt to identify the device from its auth token.
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            raw_token = auth_header.split(" ", 1)[1].strip()
            if raw_token:
                token_hash = tray_service.hash_token(raw_token)
                device = await tray_repo.get_device_by_auth_hash(token_hash)
                if device:
                    device_uid = str(device.get("device_uid", ""))

        if device_uid and _rollout_bucket(device_uid) >= rollout_percent:
            # This device is outside the current rollout window — serve the
            # most recent fully-rolled-out version instead.
            fallback = await tray_repo.get_stable_tray_version(agent_os)
            if not fallback:
                fallback = await tray_repo.get_stable_tray_version("all")
            if fallback:
                row = fallback

    return JSONResponse({
        "version": row["version"],
        "download_url": row.get("download_url"),
        "required": bool(row.get("required")),
    })


# ---------------------------------------------------------------------------
# Phase 5 – Diagnostics upload
# ---------------------------------------------------------------------------


@router.post(
    "/{device_uid}/diagnostics",
    summary="Upload a diagnostic log bundle from the tray service",
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_diagnostics(
    device_uid: str,
    request: Request,
    device: dict = Depends(get_current_tray_device),
) -> JSONResponse:
    """Accept a ZIP bundle of tray service logs uploaded by the client.

    * Capped at 20 MB.
    * Stored to the ``tray_diagnostics`` table with a path under the
      configured media directory.
    * Viewable from the admin Diagnostics page.
    """
    import os
    import uuid as _uuid

    MAX_SIZE = 20 * 1024 * 1024  # 20 MB

    content_type = request.headers.get("content-type", "application/zip")
    body = await request.body()
    if len(body) > MAX_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Diagnostic bundle exceeds {MAX_SIZE // 1024 // 1024} MB limit.",
        )

    # Determine storage path.
    media_root = _settings.media_root if hasattr(_settings, "media_root") else "media"
    diag_dir = os.path.join(str(media_root), "tray_diagnostics")
    os.makedirs(diag_dir, exist_ok=True)
    filename = f"{device_uid}_{_uuid.uuid4().hex}.zip"
    stored_path = os.path.join(diag_dir, filename)
    with open(stored_path, "wb") as f:
        f.write(body)

    await tray_repo.save_diagnostic(
        device_id=int(device["id"]),
        filename=filename,
        content_type=content_type,
        size_bytes=len(body),
        stored_path=stored_path,
    )
    log_info(
        "Tray diagnostic bundle received",
        device_uid=device_uid,
        size=len(body),
    )
    return JSONResponse({"accepted": True, "filename": filename})


# ---------------------------------------------------------------------------
# Phase 5 – Admin: diagnostics list
# ---------------------------------------------------------------------------


@router.get(
    "/admin/diagnostics",
    summary="List uploaded diagnostic bundles",
)
async def admin_list_diagnostics(
    device_id: int | None = None,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    rows = await tray_repo.list_diagnostics(device_id=device_id)
    return JSONResponse([
        {
            "id": int(r["id"]),
            "device_id": int(r["device_id"]),
            "device_uid": r.get("device_uid"),
            "hostname": r.get("hostname"),
            "filename": r["filename"],
            "size_bytes": int(r.get("size_bytes") or 0),
            "uploaded_at": r["uploaded_at"].isoformat() if hasattr(r.get("uploaded_at"), "isoformat") else str(r.get("uploaded_at")),
        }
        for r in rows
    ])


# ---------------------------------------------------------------------------
# Phase 5 – Admin: publish a new tray version
# ---------------------------------------------------------------------------


@router.post(
    "/admin/versions",
    summary="Publish a new tray installer version",
    status_code=status.HTTP_201_CREATED,
)
async def admin_publish_version(
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    body = await request.json()
    version = str(body.get("version", "")).strip()
    platform = str(body.get("platform", "all")).strip().lower()
    download_url = str(body.get("download_url", "")).strip()
    required = bool(body.get("required", False))
    release_notes = body.get("release_notes")
    rollout_percent = int(body.get("rollout_percent", 100))
    rollout_percent = max(1, min(100, rollout_percent))

    if not version or not download_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="version and download_url are required.",
        )

    await tray_repo.publish_tray_version(
        version=version,
        platform=platform,
        download_url=download_url,
        required=required,
        release_notes=release_notes,
        published_by_user_id=int(current_user["id"]),
        rollout_percent=rollout_percent,
    )
    return JSONResponse({"published": True, "version": version, "rollout_percent": rollout_percent})


@router.patch(
    "/admin/versions/{version_id}/rollout",
    summary="Update the rollout percentage for a published tray version",
)
async def admin_update_version_rollout(
    version_id: int,
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    """Incrementally widen (or reduce) the staged rollout for a version.

    Send ``{"rollout_percent": <1–100>}`` to update the percentage of
    the device fleet that will receive this version on their next poll.
    """
    body = await request.json()
    rollout_percent = int(body.get("rollout_percent", 100))
    rollout_percent = max(1, min(100, rollout_percent))
    await tray_repo.update_tray_version_rollout(version_id, rollout_percent=rollout_percent)
    return JSONResponse({"updated": True, "version_id": version_id, "rollout_percent": rollout_percent})


@router.get(
    "/admin/versions",
    summary="List published tray installer versions",
)
async def admin_list_versions(
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    rows = await tray_repo.list_tray_versions()
    total_devices = await tray_repo.count_active_devices()
    result = []
    for r in rows:
        devices_on_version = await tray_repo.count_devices_on_version(
            r["version"], r["platform"]
        )
        rollout_start = r.get("rollout_start_at")
        result.append({
            "id": int(r["id"]),
            "version": r["version"],
            "platform": r["platform"],
            "download_url": r["download_url"],
            "required": bool(r.get("required")),
            "enabled": bool(r.get("enabled")),
            "rollout_percent": int(r.get("rollout_percent") or 100),
            "rollout_start_at": (
                rollout_start.isoformat()
                if hasattr(rollout_start, "isoformat")
                else str(rollout_start) if rollout_start else None
            ),
            "devices_on_version": devices_on_version,
            "total_devices": total_devices,
            "published_at": (
                r["published_at"].isoformat()
                if hasattr(r.get("published_at"), "isoformat")
                else str(r.get("published_at"))
            ),
        })
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Phase 6 – Push notification to device
# ---------------------------------------------------------------------------


@router.post(
    "/{device_uid}/notify",
    summary="Push a notification to a connected tray device (Phase 6)",
    status_code=status.HTTP_200_OK,
)
async def push_notification_to_device(
    device_uid: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Send an OS notification to a tray device's UI agent.

    Requires the technician / super admin to be authenticated.
    The device must be active and connected (or the message is queued
    in ``tray_command_log`` for delivery on next WS reconnect —
    full queuing is Phase 5.2).
    """
    device = await tray_repo.get_device_by_uid(device_uid)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")
    if device.get("status") != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Device is not active.")

    body = await request.json()
    title = str(body.get("title", "MyPortal")).strip()[:200]
    notification_body = str(body.get("body", "")).strip()[:1000]

    if not (current_user.get("is_super_admin") or current_user.get("is_helpdesk_technician")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Helpdesk access required.")

    payload = {
        "type": "show_notification",
        "payload": {"title": title, "body": notification_body},
    }
    delivered = await tray_service.send_to_device(device_uid, payload)
    await tray_repo.log_command(
        device_id=int(device["id"]),
        command="show_notification",
        payload_json=json.dumps(payload),
        initiated_by_user_id=int(current_user["id"]),
        status="delivered" if delivered else "queued",
    )
    return JSONResponse(
        {
            "delivered": delivered,
        }
    )


# ---------------------------------------------------------------------------
# Tray chat popup — device-initiated popup chat (Phase 8)
# ---------------------------------------------------------------------------

_CHAT_TOKEN_TTL_SECONDS = 300  # 5 minutes for the one-time URL token
_POPUP_SESSION_COOKIE = "tray_popup"
_POPUP_SESSION_TTL_SECONDS = 7200  # 2-hour session for the popup window


def _build_popup_session_payload(
    *,
    device_id: int,
    room_id: int,
    company_id: int,
    csrf_token: str,
) -> str:
    """Return an encrypted cookie value for the popup chat session."""
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


def _parse_popup_session(request: Request) -> dict[str, Any] | None:
    """Decode and validate the ``tray_popup`` cookie.

    Returns the payload dict (device_id, room_id, company_id, csrf) or
    ``None`` when the cookie is missing, corrupted, or expired.
    """
    raw = request.cookies.get(_POPUP_SESSION_COOKIE)
    if not raw:
        return None
    try:
        decoded = decrypt_secret(raw)
        payload = json.loads(decoded)
    except Exception:
        return None
    exp_str = payload.get("exp", "")
    try:
        exp = datetime.fromisoformat(exp_str)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return None
    except Exception:
        return None
    return payload


@router.post(
    "/chat-token",
    response_model=TrayChatTokenResponse,
    summary="Issue a one-time popup chat token (device authenticated)",
)
async def issue_chat_token(
    request: Request,
    device: dict = Depends(get_current_tray_device),
) -> TrayChatTokenResponse:
    """Issue a short-lived one-time URL token so the tray client can open
    ``/tray/chat?token=<token>`` in a popup webview without requiring the end
    user to log into the portal manually.

    The optional ``room_id`` JSON body field pre-binds the token to an
    existing chat room (for technician-initiated chats where the room already
    exists).  When omitted, a new room is created when the popup loads.
    """
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        pass

    room_id: int | None = body.get("room_id") or None
    if room_id is not None:
        room_id = int(room_id)

    # Verify chat is enabled for this device's company.
    company_id: int | None = device.get("company_id")
    if company_id:
        company = await companies_repo.get_company_by_id(int(company_id))
        if not (company and company.get("tray_chat_enabled") and _settings.matrix_enabled):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Chat is not enabled for this device",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Device has no associated company",
        )

    # Explicit room requests come from technician-initiated chats and reply
    # notifications. Never issue a token for a closed/stale room because the
    # popup must not turn that stale launch into a brand-new user chat.
    if room_id is not None:
        requested_room = await chat_repo.get_room(int(room_id))
        if not requested_room:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat room not found")
        if requested_room.get("status") != "open":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Chat room is closed")
    else:
        # When no room was explicitly requested, reconnect to the device's
        # existing open chat room (if any) so the user continues the same
        # conversation. If none exists, the popup may create a user-initiated
        # chat when the tray icon/menu action is used.
        existing = await chat_repo.get_open_room_by_device_id(int(device["id"]))
        if existing:
            room_id = int(existing["id"])

    # Generate the one-time token.
    token = secrets.token_urlsafe(32)
    token_hash = tray_service.hash_token(token)
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=_CHAT_TOKEN_TTL_SECONDS
    )
    await tray_repo.create_chat_token(
        device_id=int(device["id"]),
        token_hash=token_hash,
        room_id=room_id,
        expires_at=expires_at,
    )

    portal_url = (_settings.public_base_url or "").rstrip("/")
    room_param = f"&room={room_id}" if room_id else ""
    chat_url = f"{portal_url}/tray/chat?token={token}{room_param}"

    log_info(
        "Tray chat token issued",
        device_uid=device.get("device_uid"),
        device_id=device.get("id"),
        room_id=room_id,
    )
    return TrayChatTokenResponse(
        token=token,
        expires_in=_CHAT_TOKEN_TTL_SECONDS,
        chat_url=chat_url,
    )


@router.get(
    "/popup-chat/{room_id}",
    summary="Get chat room state for the popup (popup-session cookie auth)",
)
async def popup_chat_get_room(
    request: Request,
    room_id: int,
) -> JSONResponse:
    """Return the chat room details and messages for the popup window.

    Authenticated by the ``tray_popup`` encrypted session cookie set when
    ``GET /tray/chat?token=...`` is visited.
    """
    session = _parse_popup_session(request)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid popup session")

    if session.get("room_id") != room_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Room mismatch")

    room = await chat_repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    messages = await chat_repo.get_messages(room_id, limit=50)

    return JSONResponse(
        {
            "room": _serialise_popup_chat_value(dict(room)),
            "messages": _serialise_popup_chat_value(messages),
            "csrf_token": session.get("csrf"),
        }
    )


@router.post(
    "/popup-chat/{room_id}/messages",
    summary="Send a chat message from the popup (popup-session cookie auth)",
)
async def popup_chat_send_message(
    request: Request,
    room_id: int,
) -> JSONResponse:
    """Send a message from the popup chat window.

    Authenticated by the ``tray_popup`` session cookie and the CSRF token
    that was embedded in the popup page.
    """
    session = _parse_popup_session(request)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid popup session")

    if session.get("room_id") != room_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Room mismatch")

    # Validate CSRF token from the X-Tray-CSRF header.
    csrf_header = request.headers.get("X-Tray-CSRF", "")
    if not secrets.compare_digest(csrf_header, session.get("csrf", "")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    message_body = str(body.get("body", "")).strip()
    if not message_body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message body is required")
    if len(message_body) > 65535:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message too long")

    room = await chat_repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    if room.get("status") != "open":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Chat room is closed")

    device_id: int = int(session["device_id"])
    device = await tray_repo.get_device_by_id(device_id)
    hostname = (device or {}).get("hostname") or "Tray Device"

    # Send via Matrix if available.
    matrix_event_id: str | None = None
    matrix_room_id = room.get("matrix_room_id") or ""
    if matrix_room_id and _settings.matrix_enabled:
        try:
            event = await matrix_service.send_message(
                room_id=matrix_room_id,
                body=message_body,
                sender_display_name=hostname,
            )
            matrix_event_id = event.get("event_id")
        except Exception as exc:
            log_error("popup_chat_send_message: Matrix send failed", error=str(exc))

    sent_at = datetime.now(timezone.utc).replace(tzinfo=None)
    msg = await chat_repo.add_message(
        room_id=room_id,
        matrix_event_id=matrix_event_id,
        sender_matrix_id=f"@tray-device-{device_id}:tray",
        body=message_body,
        sender_user_id=None,
        sender_display_name=hostname,
        sent_at=sent_at,
    )
    await chat_repo.update_room(room_id, last_message_at=sent_at, updated_at=sent_at)
    await matrix_ai_waiting_assistant.handle_user_message(room_id, sent_at)

    try:
        await chat_ticket_sync.sync_chat_message_to_ticket(
            room=room,
            message=msg,
            author_id=None,
        )
    except Exception as exc:
        log_error(
            "popup_chat_send_message: failed to sync chat message to linked ticket",
            room_id=room_id,
            error=str(exc),
        )

    # Notify portal users of new message.
    try:
        from app.services.realtime import refresh_notifier

        await refresh_notifier.broadcast_refresh(
            topics=[f"chat:room:{room_id}"],
            data={
                "message": {
                    k: (v.isoformat() if isinstance(v, datetime) else v)
                    for k, v in msg.items()
                },
                "room_id": room_id,
            },
        )
    except Exception:
        pass

    msg_data = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in msg.items()}
    await chat_ntfy_notifications.notify_chat_reply(room=room, message=msg_data, actor=None)

    return JSONResponse(
        msg_data,
        status_code=status.HTTP_201_CREATED,
    )
