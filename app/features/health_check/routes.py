"""Health check pages and APIs (:mod:`app.services.ai_health_check`).

Technicians (helpdesk technicians and super admins):

* ``GET /health-checks`` recent health checks; ``?asset_id=`` for one asset
* ``GET /health-checks/{id}`` a health check's report
* ``POST /health-checks/start`` start one from the asset page
* ``GET /api/health-checks``, ``GET /api/health-checks/{id}`` and
  ``POST /api/assets/{asset_id}/health-checks`` the same as JSON

The asset's tray device (device token):

* ``POST /api/health-checks/{id}/device-status`` progress
* ``POST /api/health-checks/{id}/device-result`` the snapshot and logs

Super admins:

* ``GET /admin/required-logs`` logs the AI troubleshooter and health check
  asked for but could not read (:mod:`app.services.ai_required_logs`)
* ``POST /admin/required-logs/{id}/status`` approve (adds the channel or
  folder to the approved list), dismiss or reopen one
* ``GET /api/required-logs`` and ``PATCH /api/required-logs/{id}`` the same as JSON
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_tray_device, require_helpdesk_technician, require_super_admin
from app.core.config import get_settings
from app.repositories import ai_health_checks as repo
from app.repositories import ai_required_logs as required_repo
from app.repositories import assets as asset_repo
from app.repositories import tickets as tickets_repo
from app.repositories import tray as tray_repo
from app.security.flash import flash_redirect
from app.services import ai_consent
from app.services import ai_health_check as service
from app.services import ai_required_logs
from app.services import ai_troubleshooter
from app.services import audit as audit_service

router = APIRouter(tags=["Health checks"])

LIST_PATH = "/health-checks"
REQUIRED_LOGS_PATH = "/admin/required-logs"
_MAX_SNAPSHOT_UPLOAD = 4 * 1024 * 1024
_MAX_LOG_UPLOAD = 16 * 1024 * 1024


def _main():
    from app import main as main_module

    return main_module


async def _technician_page(request: Request) -> tuple[dict[str, Any] | None, RedirectResponse | None]:
    main = _main()
    user, redirect = await main._require_authenticated_user(request)
    if redirect:
        return None, redirect
    if not await main._is_helpdesk_technician(user, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Helpdesk technician access required")
    return user, None


def _json(value: Any) -> Any:
    import json

    return json.loads(json.dumps(value, default=str))


# --------------------------------------------------------------------------- #
# Starting a health check
# --------------------------------------------------------------------------- #


async def _start(
    *,
    asset_id: int,
    user: dict[str, Any],
    allow_pawnio: bool | None,
    ticket_id: int | None,
    model: str | None,
    request: Request,
) -> dict[str, Any]:
    asset = await asset_repo.get_asset_by_id(asset_id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
    if asset.get("archived_at"):
        raise service.HealthCheckError("This asset is archived.")
    device = await repo.find_tray_device_for_asset(asset_id)
    if not device:
        raise service.HealthCheckError("This asset has no active tray device to run the health check on.")
    ticket = None
    if ticket_id:
        ticket = await tickets_repo.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found.")
        if ticket.get("company_id") not in (None, asset.get("company_id")):
            raise service.HealthCheckError("The ticket belongs to a different company.", 403)
        if not await ai_consent.is_ai_allowed_for_ticket(ticket):
            raise service.HealthCheckError(
                "The ticket's requester has opted out of AI processing, so health check notes cannot be "
                "posted to it. Start the health check from the asset instead.", 403,
            )
        linked = await tickets_repo.list_ticket_assets(int(ticket["id"]))
        if not any(item.get("asset_id") == asset_id for item in linked):
            raise service.HealthCheckError("The asset is not linked to the ticket.", 400)
    result = await service.start_health_check(
        asset=asset, device=device, requested_by=user, ticket=ticket, allow_pawnio=allow_pawnio, model=model,
    )
    await audit_service.log_action(
        action="health_check.start", user_id=user.get("id"), entity_type="ai_health_check",
        entity_id=result["id"], new_value={"asset_id": asset_id, "ticket_id": ticket_id,
                                           "allow_pawnio": result["allow_pawnio"]},
        request=request,
    )
    return result


@router.post(f"{LIST_PATH}/start", response_class=HTMLResponse, summary="Start a health check from the asset page")
async def start_from_form(request: Request):
    user, redirect = await _technician_page(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        asset_id = int(str(form.get("asset_id") or ""))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose an asset.") from None
    allow_pawnio = str(form.get("allow_pawnio") or "").lower() in {"1", "on", "true", "yes"}
    try:
        result = await _start(asset_id=asset_id, user=user, allow_pawnio=allow_pawnio, ticket_id=None,
                              model=None, request=request)
    except service.HealthCheckError as exc:
        return flash_redirect(f"/assets/{asset_id}#health-check", str(exc), "error")
    message = (
        "Health check started. This report fills in as the device and the AI model finish."
        if result["delivered"]
        else "Health check queued. The device is offline and will run it when it reconnects."
    )
    return flash_redirect(service.report_url(result["id"]), message, "success")


class StartRequest(BaseModel):
    allow_pawnio: bool | None = Field(
        default=None,
        description="Install PawnIO for the check on physical Windows hardware (default: HEALTH_CHECK_PAWNIO_ENABLED)",
    )
    model: str | None = Field(default=None, max_length=255, description="Override the LLM model")
    ticket_id: int | None = Field(default=None, gt=0, description="Post progress and the report to this ticket")


@router.post(
    "/api/assets/{asset_id}/health-checks",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an AI health check on an asset's computer",
)
async def api_start(
    asset_id: int,
    request: Request,
    payload: StartRequest | None = None,
    current_user: dict = Depends(require_helpdesk_technician),
):
    payload = payload or StartRequest()
    try:
        result = await _start(asset_id=asset_id, user=current_user, allow_pawnio=payload.allow_pawnio,
                              ticket_id=payload.ticket_id, model=payload.model, request=request)
    except service.HealthCheckError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return JSONResponse({**result, "url": service.report_url(result["id"])}, status_code=status.HTTP_202_ACCEPTED)


# --------------------------------------------------------------------------- #
# Viewing health checks
# --------------------------------------------------------------------------- #


def _summary_view(check: dict[str, Any]) -> dict[str, Any]:
    item = service.present(check)
    for key in ("snapshot", "report", "logs"):
        item.pop(key, None)
    return item


@router.get(LIST_PATH, response_class=HTMLResponse, summary="Recent health checks")
async def list_page(request: Request, asset_id: int | None = Query(default=None, gt=0)):
    user, redirect = await _technician_page(request)
    if redirect:
        return redirect
    await service.expire_stale()
    checks = [service.present(check) for check in await repo.list_checks(asset_id=asset_id, limit=200)]
    asset = await asset_repo.get_asset_by_id(asset_id) if asset_id else None
    counts = {key: 0 for key in service.OVERALL_LABELS}
    latest_per_asset: set[int] = set()
    for check in checks:
        if check.get("status") == service.STATUS_COMPLETED and int(check["asset_id"]) not in latest_per_asset:
            latest_per_asset.add(int(check["asset_id"]))
            counts[str(check.get("overall_status") or "unknown")] = counts.get(str(check.get("overall_status") or "unknown"), 0) + 1
    return await _main()._render_template(
        "health_checks/index.html", request, user,
        extra={
            "title": "Health checks",
            "checks": checks,
            "filter_asset": asset,
            "counts": counts,
            "running": sum(1 for check in checks if check["is_active"]),
            "status_labels": service.STATUS_LABELS,
        },
    )


def _snapshot_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    sections = snapshot.get("sections") if isinstance(snapshot.get("sections"), dict) else {}
    sensors = snapshot.get("sensors") if isinstance(snapshot.get("sensors"), dict) else {}
    readings = [r for r in sensors.get("readings") or [] if isinstance(r, dict)]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for reading in readings:
        grouped.setdefault(str(reading.get("type") or "Other"), []).append(reading)
    return {
        "sections": sections,
        "errors": snapshot.get("errors") if isinstance(snapshot.get("errors"), dict) else {},
        "sensor_groups": grouped,
        "sensor_status": str(sensors.get("status") or ""),
        "sensor_detail": str(sensors.get("detail") or ""),
        "is_virtual": snapshot.get("is_virtual"),
        "hardware": snapshot.get("hardware_identity") if isinstance(snapshot.get("hardware_identity"), dict) else {},
        "collected_at": snapshot.get("collected_at"),
        "duration_ms": snapshot.get("duration_ms"),
    }


@router.get(f"{LIST_PATH}/{{health_check_id}}", response_class=HTMLResponse, summary="A health check report")
async def report_page(request: Request, health_check_id: int):
    user, redirect = await _technician_page(request)
    if redirect:
        return redirect
    await service.expire_stale()
    check = await repo.get(health_check_id)
    if not check:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Health check not found")
    report = check.get("report") or {}
    signals = report.get("signals") if check.get("status") == service.STATUS_COMPLETED else None
    if signals is None:
        signals = service.evaluate_snapshot(check.get("snapshot"))
    previous = [
        service.present(item) for item in await repo.list_checks(asset_id=int(check["asset_id"]), limit=10)
        if int(item["id"]) != health_check_id
    ]
    return await _main()._render_template(
        "health_checks/report.html", request, user,
        extra={
            "title": f"Health check #{health_check_id}",
            "check": service.present(check),
            "report": report,
            "signals": signals,
            "snapshot": _snapshot_view(check.get("snapshot") or {}),
            "logs": [s for s in (check.get("logs") or {}).get("sources") or [] if isinstance(s, dict)],
            "areas": service.AREAS,
            "previous": previous,
            "is_super_admin": bool(user.get("is_super_admin")),
        },
    )


@router.get("/api/health-checks", summary="List health checks")
async def api_list(
    asset_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(require_helpdesk_technician),
):
    checks = await repo.list_checks(asset_id=asset_id, limit=limit)
    return JSONResponse({"health_checks": _json([_summary_view(check) for check in checks])})


@router.get("/api/health-checks/{health_check_id}", summary="A health check and its report")
async def api_get(health_check_id: int, current_user: dict = Depends(require_helpdesk_technician)):
    check = await repo.get(health_check_id)
    if not check:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Health check not found")
    item = service.present(check)
    item.pop("logs", None)
    return JSONResponse({"health_check": _json(item)})


async def asset_context(asset: dict[str, Any], *, can_run: bool) -> dict[str, Any]:
    """The asset page's Health check section."""

    asset_id = int(asset["id"])
    device = await repo.find_tray_device_for_asset(asset_id)
    platform = ai_troubleshooter.endpoint_platform(device, asset) if device else "unknown"
    pawnio_enabled = bool(getattr(get_settings(), "health_check_pawnio_enabled", True))
    return {
        "checks": [service.present(check) for check in await repo.list_checks(asset_id=asset_id, limit=5)],
        "device": {"hostname": device.get("hostname"), "last_seen_utc": device.get("last_seen_utc")} if device else None,
        "can_run": can_run and bool(device),
        "platform": platform,
        "pawnio_available": pawnio_enabled and platform == "windows",
        "pawnio_enabled": pawnio_enabled,
    }


# --------------------------------------------------------------------------- #
# Device callbacks
# --------------------------------------------------------------------------- #


async def _resolve(health_check_id: int, command_id: int, device: dict[str, Any]) -> tuple[dict, dict, dict]:
    command = await tray_repo.get_command(command_id)
    if command is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command not found")
    if command.get("command") != service.COMMAND:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Command is not a health check command")
    if command.get("device_id") != device.get("id"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Command was not issued to this device")
    payload = ai_troubleshooter.command_payload(command)
    try:
        payload_check_id = int(payload.get("health_check_id") or 0)
    except (TypeError, ValueError):
        payload_check_id = 0
    if payload_check_id != health_check_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Command does not target this health check")
    check = await repo.get(health_check_id)
    if not check:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Health check not found")
    if check.get("tray_device_id") != device.get("id") or check.get("company_id") != device.get("company_id"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Health check is not for this device")
    if check.get("command_id") != command_id or str(command.get("status") or "") in {"completed", "error"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This health check command has finished")
    if check.get("status") not in service.ACTIVE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This health check has finished")
    return check, command, payload


@router.post("/api/health-checks/{health_check_id}/device-status", summary="Health check progress from a tray device")
async def device_status(
    health_check_id: int,
    command_id: int = Form(..., gt=0),
    stage: str = Form(..., max_length=32),
    message: str = Form("", max_length=4096),
    device: dict = Depends(get_current_tray_device),
):
    stage_key = stage.strip().lower()
    if stage_key not in service.DEVICE_STAGES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown health check stage")
    check, command, _payload = await _resolve(health_check_id, command_id, device)
    await service.record_device_status(check, command, stage_key, message.strip()[:1000])
    return JSONResponse({"status": "ok", "health_check_id": health_check_id, "stage": stage_key})


async def _read_upload(upload: UploadFile | None, limit: int, what: str) -> bytes:
    if upload is None:
        return b""
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"The {what} is too large")
    return data


@router.post("/api/health-checks/{health_check_id}/device-result", summary="Health check results from a tray device")
async def device_result(
    health_check_id: int,
    command_id: int = Form(..., gt=0),
    phase: str = Form(..., max_length=16),
    error: str = Form("", max_length=4096),
    snapshot: UploadFile | None = File(None),
    log_bundle: UploadFile | None = File(None),
    device: dict = Depends(get_current_tray_device),
):
    check, command, payload = await _resolve(health_check_id, command_id, device)
    phase_key = phase.strip().lower()
    if phase_key not in {service.PHASE_SNAPSHOT, service.PHASE_LOGS} or phase_key != payload.get("phase"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unexpected health check phase")
    snapshot_bytes = await _read_upload(snapshot, _MAX_SNAPSHOT_UPLOAD, "snapshot")
    log_bytes = await _read_upload(log_bundle, _MAX_LOG_UPLOAD, "log bundle")
    queued = await service.receive_device_result(
        check, command, phase=phase_key, snapshot_text=snapshot_bytes, log_bytes=log_bytes, error=error,
    )
    return JSONResponse({"status": "accepted", "health_check_id": health_check_id, "analysis_queued": queued})


# --------------------------------------------------------------------------- #
# Required logs (super admins)
# --------------------------------------------------------------------------- #

REASON_LABELS = {
    ai_required_logs.REASON_NOT_ALLOWED: "Not on the approved list",
    ai_required_logs.REASON_UNAVAILABLE: "Unavailable on the endpoint",
}
STATUS_LABELS = {"open": "Needs review", "approved": "Approved", "dismissed": "Dismissed"}


def _currently_allowed(entry: dict[str, Any]) -> bool:
    platform = str(entry.get("platform") or "unknown")
    source = str(entry.get("source") or "")
    if source in ai_troubleshooter.allowed_log_sources(platform):
        return True
    return ai_troubleshooter.normalise_file_source(source, platform) is not None


def _present_entry(entry: dict[str, Any]) -> dict[str, Any]:
    item = dict(entry)
    item["kind_label"] = ai_required_logs.KIND_LABELS.get(str(entry.get("kind")), "Log")
    item["reason_label"] = REASON_LABELS.get(str(entry.get("reason_code")), str(entry.get("reason_code")))
    item["status_label"] = STATUS_LABELS.get(str(entry.get("status")), str(entry.get("status")))
    item["feature_labels"] = [
        ai_required_logs.FEATURE_LABELS.get(feature, feature)
        for feature in str(entry.get("features") or "").split(",") if feature
    ]
    item["can_approve"] = (
        entry.get("reason_code") == ai_required_logs.REASON_NOT_ALLOWED and bool(entry.get("approve_kind"))
    )
    item["approve_label"] = {
        ai_required_logs.APPROVE_EVENT_LOG: "Event log channel to allow",
        ai_required_logs.APPROVE_FOLDER: "Log folder to allow",
    }.get(str(entry.get("approve_kind") or ""), "")
    item["currently_allowed"] = (
        entry.get("reason_code") == ai_required_logs.REASON_NOT_ALLOWED and _currently_allowed(entry)
    )
    changed = entry.get("status_changed_at")
    item["seen_since_review"] = bool(
        changed and entry.get("status") == "dismissed" and entry.get("last_seen_at") and entry["last_seen_at"] > changed
    )
    return item


def _approved_summary() -> dict[str, Any]:
    settings = get_settings()
    return {
        "env_event_logs": bool(str(getattr(settings, "troubleshoot_event_logs_windows", "") or "").strip()),
        "env_folders": bool(str(settings.troubleshoot_log_folders_windows or "").strip()
                            or str(settings.troubleshoot_log_folders_macos or "").strip()),
        "event_logs": ai_required_logs.approved_values("windows", ai_required_logs.APPROVE_EVENT_LOG),
        "folders": [
            *ai_required_logs.approved_values("windows", ai_required_logs.APPROVE_FOLDER),
            *ai_required_logs.approved_values("macos", ai_required_logs.APPROVE_FOLDER),
        ],
    }


@router.get(REQUIRED_LOGS_PATH, response_class=HTMLResponse, summary="Logs the AI agents need but cannot read")
async def required_logs_page(
    request: Request, view: str = Query(default="open", pattern="^(open|approved|dismissed|all)$")
):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    await ai_required_logs.refresh_approvals(force=True)
    entries = [_present_entry(entry) for entry in await required_repo.list_entries(limit=1000)]
    counts = {key: sum(1 for e in entries if e.get("status") == key) for key in STATUS_LABELS}
    shown = entries if view == "all" else [e for e in entries if e.get("status") == view]
    return await _main()._render_template(
        "health_checks/required_logs.html", request, user,
        extra={
            "title": "Required logs",
            "entries": shown,
            "view": view,
            "counts": counts,
            "status_labels": STATUS_LABELS,
            "approved": _approved_summary(),
        },
    )


async def _change_entry(
    entry_id: int, action: str, *, value: str, note: str, user: dict[str, Any], request: Request
) -> dict[str, Any]:
    entry = await required_repo.get(entry_id)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Required log not found")
    approve_value: str | None = None
    if action == "approve":
        if entry.get("reason_code") != ai_required_logs.REASON_NOT_ALLOWED or not entry.get("approve_kind"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Only logs that are not on the approved list can be approved.")
        approve_value = ai_required_logs.validate_approval(
            str(entry["approve_kind"]), value or str(entry.get("approve_value") or ""), str(entry.get("platform"))
        )
        if not approve_value:
            detail = (
                "Enter a valid event log channel name, such as Microsoft-Windows-Ntfs/Operational."
                if entry["approve_kind"] == ai_required_logs.APPROVE_EVENT_LOG
                else "Enter a full folder path that names at least two folders, such as C:\\ProgramData\\Vendor\\Logs."
            )
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
        new_status = "approved"
    elif action == "dismiss":
        new_status = "dismissed"
    elif action == "reopen":
        new_status = "open"
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown action")
    await required_repo.set_status(entry_id, status=new_status, note=note.strip()[:500] or None,
                                   user_id=user.get("id"), approve_value=approve_value)
    await ai_required_logs.refresh_approvals(force=True)
    await audit_service.log_action(
        action=f"ai_required_logs.{action}", user_id=user.get("id"), entity_type="ai_required_log",
        entity_id=entry_id, previous_value={"status": entry.get("status"), "approve_value": entry.get("approve_value")},
        new_value={"status": new_status, "approve_value": approve_value or entry.get("approve_value")},
        request=request,
    )
    return {**entry, "status": new_status, "approve_value": approve_value or entry.get("approve_value")}


@router.post(f"{REQUIRED_LOGS_PATH}/{{entry_id}}/status", response_class=HTMLResponse,
             summary="Approve, dismiss or reopen a required log")
async def change_entry_status(request: Request, entry_id: int):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    action = str(form.get("action") or "")
    view = str(form.get("view") or "open")
    view = view if view in {"open", "approved", "dismissed", "all"} else "open"
    target = f"{REQUIRED_LOGS_PATH}?view={view}#required-log-{entry_id}"
    try:
        entry = await _change_entry(entry_id, action, value=str(form.get("approve_value") or ""),
                                    note=str(form.get("note") or ""), user=user, request=request)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise
        return flash_redirect(target, str(exc.detail), "error")
    messages = {
        "approve": f"Approved. {entry.get('approve_value')} is now on the approved list for the AI troubleshooter "
                   "and health check.",
        "dismiss": "Dismissed.",
        "reopen": "Reopened.",
    }
    return flash_redirect(target, messages[action], "success")


class EntryChange(BaseModel):
    action: str = Field(pattern="^(approve|dismiss|reopen)$")
    approve_value: str = Field(default="", max_length=512)
    note: str = Field(default="", max_length=500)


@router.get("/api/required-logs", summary="Logs the AI agents need but cannot read")
async def api_required_logs(
    view: str = Query(default="all", pattern="^(open|approved|dismissed|all)$"),
    current_user: dict = Depends(require_super_admin),
):
    await ai_required_logs.refresh_approvals(force=True)
    entries = await required_repo.list_entries(status=None if view == "all" else view, limit=1000)
    return JSONResponse({"required_logs": _json([_present_entry(entry) for entry in entries]),
                         "approved": _approved_summary()})


@router.patch("/api/required-logs/{entry_id}", summary="Approve, dismiss or reopen a required log")
async def api_change_entry(
    entry_id: int, payload: EntryChange, request: Request, current_user: dict = Depends(require_super_admin)
):
    entry = await _change_entry(entry_id, payload.action, value=payload.approve_value, note=payload.note,
                                user=current_user, request=request)
    return JSONResponse({"required_log": _json(_present_entry(entry))})
