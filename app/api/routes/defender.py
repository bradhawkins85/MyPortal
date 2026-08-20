"""Company-scoped Windows Defender UI and tray-agent API."""
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.api.routes.tray import _resolve_tray_device
from app.repositories import defender as repo
from app.schemas.defender import (DefenderCommandResult, DefenderDetectionAction,
    DefenderDetectionReport, DefenderExclusionCreate, DefenderSettingsUpdate, DefenderStatusReport)
from app.services import tickets as tickets_service
from app.services import audit as audit_service
from app.repositories import tickets as tickets_repo

router = APIRouter(tags=["Windows Defender"])


def _main():
    """Import the main module lazily to avoid a router import cycle."""
    from app import main as main_module

    return main_module


async def _portal_context(request: Request, *, write: bool = False):
    user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return None, None, None, redirect
    company_id = user.get("company_id")
    if company_id is None:
        raise HTTPException(400, "No active company")
    membership = None
    can_write = bool(user.get("is_super_admin")) or bool(user.get("is_company_admin"))
    if write and not can_write:
        return user, membership, int(company_id), RedirectResponse("/", status_code=303)
    return user, membership, int(company_id), None

@router.get("/defender", response_class=HTMLResponse)
async def defender_page(request: Request):
    user, membership, company_id, redirect = await _portal_context(request)
    if redirect:
        return redirect
    enabled = await repo.company_enabled(company_id)
    devices, exclusions, detections = await repo.dashboard(company_id) if enabled else ([], [], [])
    defender_settings = await repo.settings(company_id) if enabled else {}
    return JSONResponse({
        "defender_enabled": enabled,
        "defender_devices": devices,
        "defender_exclusions": exclusions,
        "defender_detections": detections,
        "defender_can_write": bool(user.get("is_super_admin")) or bool(user.get("is_company_admin")),
        "defender_settings": defender_settings,
    })

@router.post("/api/defender/exclusions", response_class=JSONResponse)
async def create_exclusion(payload: DefenderExclusionCreate, request: Request):
    user, _, company_id, redirect = await _portal_context(request, write=True)
    if redirect:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read/write Defender access required")
    if not await repo.company_enabled(company_id):
        raise HTTPException(409, "Windows Defender management is disabled for this company")
    if payload.scope == "global" and not user.get("is_super_admin"):
        raise HTTPException(403, "Only super administrators can manage global exclusions")
    if payload.scope == "device" and payload.tray_device_id is None:
        raise HTTPException(422, "A device is required for device exclusions")
    if payload.tray_device_id and not await repo.device_belongs_to_company(payload.tray_device_id, company_id):
        raise HTTPException(404, "Device not found in the active company")
    await repo.add_exclusion(payload.scope, company_id, payload.tray_device_id, payload.exclusion_type, payload.value, user["id"])
    await audit_service.log_action(action="defender.exclusion.created", user_id=user["id"], entity_type="defender_exclusion",
        new_value=payload.model_dump(), metadata={"company_id": company_id}, request=request)
    return {"status": "created"}

@router.delete("/api/defender/exclusions/{exclusion_id}")
async def remove_exclusion(exclusion_id: int, request: Request):
    user, _, company_id, redirect = await _portal_context(request, write=True)
    if redirect:
        raise HTTPException(403, "Read/write Defender access required")
    await repo.delete_exclusion(exclusion_id, company_id, bool(user.get("is_super_admin")))
    await audit_service.log_action(action="defender.exclusion.deleted", user_id=user["id"], entity_type="defender_exclusion",
        entity_id=exclusion_id, metadata={"company_id": company_id}, request=request)
    return {"status": "deleted"}

@router.post("/api/defender/enabled")
async def set_defender_enabled(request: Request):
    user, _, company_id, redirect = await _portal_context(request)
    if redirect or not user.get("is_super_admin"):
        raise HTTPException(403, "Super administrator access required")
    payload = await request.json()
    await repo.set_company_enabled(company_id, bool(payload.get("enabled")))
    await audit_service.log_action(action="defender.configuration.updated", user_id=user["id"], entity_type="company",
        entity_id=company_id, new_value={"defender_enabled": bool(payload.get("enabled"))}, request=request)
    return {"enabled": bool(payload.get("enabled"))}

@router.put("/api/defender/settings")
async def update_defender_settings(payload: DefenderSettingsUpdate, request: Request):
    user, _, company_id, redirect = await _portal_context(request, write=True)
    if redirect:
        raise HTTPException(403, "Read/write Defender access required")
    if payload.scheduled_scan_type and (payload.scheduled_scan_day is None or payload.scheduled_scan_time is None):
        raise HTTPException(422, "Scheduled scans require a day and time")
    await repo.update_settings(company_id, payload)
    await audit_service.log_action(action="defender.policy.updated", user_id=user["id"], entity_type="company",
        entity_id=company_id, new_value=payload.model_dump(), request=request)
    return {"status": "updated"}

@router.post("/api/defender/devices/{device_id}/commands/{command_type}")
async def create_defender_command(device_id: int, command_type: str, request: Request):
    user, _, company_id, redirect = await _portal_context(request, write=True)
    if redirect:
        raise HTTPException(403, "Read/write Defender access required")
    if command_type not in {"quick_scan", "full_scan", "signature_update"}:
        raise HTTPException(422, "Unsupported Defender command")
    if not await repo.device_belongs_to_company(device_id, company_id):
        raise HTTPException(404, "Device not found")
    command_id = await repo.queue_command(company_id, device_id, command_type, user["id"])
    await audit_service.log_action(action="defender.command.queued", user_id=user["id"], entity_type="defender_command",
        entity_id=command_id, metadata={"company_id": company_id, "device_id": device_id, "command": command_type}, request=request)
    return {"id": command_id, "status": "pending"}

@router.post("/api/defender/detections/{detection_id}/actions")
async def detection_action(detection_id: int, payload: DefenderDetectionAction, request: Request):
    user, _, company_id, redirect = await _portal_context(request, write=True)
    if redirect:
        raise HTTPException(403, "Read/write Defender access required")
    row = await repo.detection(detection_id, company_id)
    if not row:
        raise HTTPException(404, "Detection not found")
    if payload.action in {"quarantine", "remediate"}:
        command_id = await repo.queue_command(company_id, row["tray_device_id"], payload.action, user["id"], detection_id)
        await audit_service.log_action(action=f"defender.detection.{payload.action}.queued", user_id=user["id"],
            entity_type="defender_command", entity_id=command_id, metadata={"company_id": company_id, "detection_id": detection_id}, request=request)
        return {"id": command_id, "status": "pending"}
    updated = await repo.update_detection_workflow(detection_id, company_id, user["id"], payload.action)
    await audit_service.log_action(action=f"defender.detection.{payload.action}", user_id=user["id"], entity_type="defender_detection",
        entity_id=detection_id, metadata={"company_id": company_id}, request=request)
    return {"status": updated.get("status"), "action": payload.action}

@router.post("/api/defender/detections/{detection_id}/ticket")
async def create_detection_ticket(detection_id: int, request: Request):
    user, _, company_id, redirect = await _portal_context(request, write=True)
    if redirect:
        raise HTTPException(403, "Read/write Defender access required")
    row = await repo.detection(detection_id, company_id)
    if not row:
        raise HTTPException(404, "Detection not found")
    if row.get("ticket_id"):
        return {"ticket_id": row["ticket_id"], "url": f"/tickets/{row['ticket_id']}"}
    ticket = await tickets_service.create_ticket(subject=f"Defender detection: {row['threat_name']}",
        description=f"Windows Defender reported **{row['threat_name']}** ({row['severity']}) on device **{row.get('hostname') or row['tray_device_id']}**.\n\nDetection reference: {row['detection_uid']}",
        requester_id=user["id"], company_id=company_id, assigned_user_id=None, priority="high" if row["severity"] in ("high","critical") else "normal",
        status="open", category="Windows Defender", module_slug=None, external_reference=f"defender:{row['id']}")
    await repo.link_ticket(detection_id, ticket["id"])
    if row.get("asset_id"):
        await tickets_repo.replace_ticket_assets(ticket["id"], [row["asset_id"]])
    return {"ticket_id": ticket["id"], "url": f"/tickets/{ticket['id']}"}

@router.post("/api/defender/devices/{device_id}/ticket")
async def create_device_ticket(device_id: int, request: Request):
    """Create a ticket for a protection issue and link the endpoint asset."""
    user, _, company_id, redirect = await _portal_context(request, write=True)
    if redirect:
        raise HTTPException(403, "Read/write Defender access required")
    row = await repo.device(device_id, company_id)
    if not row:
        raise HTTPException(404, "Device not found")
    payload = await request.json()
    issue = str(payload.get("issue") or "Windows Defender requires investigation").strip()[:1000]
    hostname = row.get("hostname") or f"Device #{device_id}"
    description = (f"Windows Defender issue reported for **{hostname}**.\n\n{issue}\n\n"
        f"Health: {row.get('health_status') or 'unknown'}; antivirus: {'on' if row.get('antivirus_enabled') else 'off'}; "
        f"real-time protection: {'on' if row.get('realtime_protection_enabled') else 'off'}; active detections: {row.get('threat_count') or 0}.")
    ticket = await tickets_service.create_ticket(subject=f"Defender issue: {hostname}", description=description,
        requester_id=user["id"], company_id=company_id, assigned_user_id=None, priority="normal",
        status="open", category="Windows Defender", module_slug=None, external_reference=f"defender-device:{device_id}")
    if row.get("asset_id"):
        await tickets_repo.replace_ticket_assets(ticket["id"], [row["asset_id"]])
    return {"ticket_id": ticket["id"], "url": f"/tickets/{ticket['id']}"}

async def _tray(request: Request):
    device = await _resolve_tray_device(type("Payload", (), {"device_uid": None})(), request)
    if not await repo.company_enabled(int(device["company_id"])):
        raise HTTPException(404, "Windows Defender management is not enabled")
    return device

@router.get("/api/tray/defender/policy")
async def tray_policy(request: Request):
    device = await _tray(request)
    result = await repo.policy(int(device["id"]), int(device["company_id"]))
    configured = await repo.settings(int(device["company_id"]))
    result["scheduled_scan"] = {
        "type": configured.get("defender_scheduled_scan_type"),
        "day": configured.get("defender_scheduled_scan_day"),
        "time": configured.get("defender_scheduled_scan_time"),
    }
    return result

@router.get("/api/tray/defender/commands")
async def tray_commands(request: Request):
    device = await _tray(request)
    return {"commands": await repo.poll_commands(int(device["id"]), int(device["company_id"]))}

@router.post("/api/tray/defender/commands/{command_id}/result")
async def tray_command_result(command_id: int, payload: DefenderCommandResult, request: Request):
    device = await _tray(request)
    await repo.complete_command(command_id, int(device["id"]), payload.status, payload.result)
    return {"status": "accepted"}

@router.post("/api/tray/defender/status")
async def tray_status(payload: DefenderStatusReport, request: Request):
    device = await _tray(request)
    await repo.report_status(int(device["id"]), int(device["company_id"]), payload)
    return {"status": "accepted"}

@router.post("/api/tray/defender/detections")
async def tray_detection(payload: DefenderDetectionReport, request: Request):
    device = await _tray(request)
    detection = await repo.report_detection(int(device["id"]), int(device["company_id"]), payload)
    settings = await repo.settings(int(device["company_id"]))
    threshold = settings.get("defender_auto_ticket_min_severity")
    rank = {"low": 1, "medium": 2, "high": 3, "critical": 4, "unknown": 0}
    if detection and threshold and not detection.get("ticket_id") and rank.get(payload.severity, 0) >= rank.get(str(threshold), 99):
        ticket = await tickets_service.create_ticket(subject=f"Defender detection: {payload.threat_name}",
            description=f"Windows Defender automatically reported **{payload.threat_name}** ({payload.severity}) on tray device #{device['id']}.",
            requester_id=None, company_id=int(device["company_id"]), assigned_user_id=None,
            priority="high" if payload.severity in {"high", "critical"} else "normal", status="open",
            category="Windows Defender", module_slug=None, external_reference=f"defender:{detection['id']}", send_creation_notification=False)
        await repo.link_ticket(detection["id"], ticket["id"])
    return {"status": "accepted"}
