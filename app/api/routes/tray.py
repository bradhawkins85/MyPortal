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

import json
from datetime import datetime, timezone
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
from app.repositories import chat as chat_repo
from app.repositories import companies as companies_repo
from app.repositories import tray as tray_repo
from app.schemas.tray import (
    TrayChatStartRequest,
    TrayChatStartResponse,
    TrayConfigResponse,
    TrayEnrolRequest,
    TrayEnrolResponse,
    TrayHeartbeatRequest,
    TrayInstallTokenCreate,
    TrayInstallTokenResponse,
    TrayMenuConfigCreate,
    TrayMenuConfigUpdate,
)
from app.services import audit as audit_service
from app.services import matrix as matrix_service
from app.services import tray as tray_service
from app.services.sanitization import sanitize_rich_text

router = APIRouter(prefix="/api/tray", tags=["Tray App"])

_settings = get_settings()


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
        env_allowlist=config.get("env_allowlist") or [],
        chat_enabled=chat_enabled,
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

    delivered = await tray_service.send_to_device(
        device_uid,
        {
            "type": "chat_open",
            "room_id": int(room["id"]),
            "matrix_room_id": matrix_room_id,
            "subject": subject,
            "initiated_by": current_user.get("display_name")
            or current_user.get("email"),
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
# Helpers
# ---------------------------------------------------------------------------


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


@router.get(
    "/version",
    summary="Check the latest available tray installer version",
    tags=["Tray App"],
)
async def get_tray_version(request: Request) -> JSONResponse:
    """Public endpoint; devices poll this on a 6-hour timer.

    Returns the latest enabled version record so the service can
    compare against ``AgentVersion`` and download the signed installer
    if a newer version is available.
    """
    import platform as _platform

    agent_os = request.headers.get("X-Tray-OS", "all").lower()
    row = await tray_repo.get_latest_tray_version(agent_os)
    if not row:
        # Fall back to 'all' platform.
        row = await tray_repo.get_latest_tray_version("all")
    if not row:
        return JSONResponse({"version": "0.0.0", "download_url": None, "required": False})
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
    )
    return JSONResponse({"published": True, "version": version})


@router.get(
    "/admin/versions",
    summary="List published tray installer versions",
)
async def admin_list_versions(
    current_user: dict = Depends(require_super_admin),
) -> JSONResponse:
    rows = await tray_repo.list_tray_versions()
    return JSONResponse([
        {
            "id": int(r["id"]),
            "version": r["version"],
            "platform": r["platform"],
            "download_url": r["download_url"],
            "required": bool(r.get("required")),
            "enabled": bool(r.get("enabled")),
            "published_at": r["published_at"].isoformat() if hasattr(r.get("published_at"), "isoformat") else str(r.get("published_at")),
        }
        for r in rows
    ])


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

    payload = {"type": "show_notification", "title": title, "body": notification_body}
    delivered = await tray_service.send_to_device(device_uid, payload)

    # Always log the command for the audit trail.
    import json as _json
    await tray_repo.log_command(
        device_id=int(device["id"]),
        command="show_notification",
        payload_json=_json.dumps(payload),
        initiated_by_user_id=int(current_user["id"]),
        status="delivered" if delivered else "queued",
    )
    return JSONResponse({"delivered": delivered})
