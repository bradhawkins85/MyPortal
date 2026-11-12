"""Windows blue screen (minidump) routes (:mod:`app.services.minidump_analysis`).

The asset's tray device (device token):

* ``POST /api/tray/minidumps`` report the dumps it decoded. Only a summary of
  each dump is sent; the dump file stays on the computer.

Staff with RMM scripts access to the asset's company (write access to analyse):

* ``GET /api/assets/{asset_id}/minidumps`` the asset's dumps and their analyses
* ``POST /api/assets/{asset_id}/minidumps/{dump_id}/analyse`` have the AI
  troubleshooter explain a crash, optionally posting the result to one of
  the asset's tickets
* ``POST /rmm/assets/{asset_id}/minidumps/{dump_id}/analyse`` the same from
  the asset page's Minidumps section
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_tray_device
from app.repositories import ai_health_checks as health_repo
from app.repositories import asset_minidumps as repo
from app.repositories import assets as asset_repo
from app.security.flash import flash_redirect
from app.services import ai_troubleshooter
from app.services import audit as audit_service
from app.services import minidump_analysis as service
from app.services import tray as tray_service

from .routes import _rmm_access

router = APIRouter(tags=["Minidumps"])

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
UPLOADS_PER_WINDOW = 6
UPLOAD_WINDOW_SECONDS = 600.0
_CLOSED_TICKET_STATUSES = {"closed", "resolved"}

# device id -> times of its recent uploads
_UPLOADS: dict[int, list[float]] = {}


def allow_upload(device_id: int, now: float, uploads: dict[int, list[float]] | None = None) -> bool:
    """Whether the device may upload now: at most six reports in ten minutes."""

    uploads = _UPLOADS if uploads is None else uploads
    recent = [at for at in uploads.get(device_id, []) if now - at < UPLOAD_WINDOW_SECONDS]
    allowed = len(recent) < UPLOADS_PER_WINDOW
    if allowed:
        recent.append(now)
    uploads[device_id] = recent
    for other in [key for key, times in uploads.items() if not times or now - times[-1] >= UPLOAD_WINDOW_SECONDS]:
        if other != device_id:
            del uploads[other]
    return allowed


class UploadResult(BaseModel):
    status: str = Field(description="Always ``ok``")
    received: int = Field(description="Dumps read from the request (at most 20)")
    stored: int = Field(description="Dumps stored for the first time")
    duplicates: int = Field(description="Dumps this device had already reported")
    rejected: int = Field(description="Entries that could not be identified as a dump")


class Dump(BaseModel):
    id: int
    file_name: str
    crashed_at: Any = Field(default=None, description="Crash time (UTC)")
    stop_code: str = Field(description="Stop code and its name, e.g. 0x000000D1 DRIVER_IRQL_NOT_LESS_OR_EQUAL")
    parameter_meanings: list[str] = Field(default_factory=list)
    suspect_driver: str = Field(description="Driver the dump points at, when one stands out")
    uptime_label: str
    supported: bool = Field(description="False when the tray app could not decode the dump")
    error: str
    analysis_status: str = Field(description="none, analysing, completed or failed")
    status_label: str
    status_variant: str
    analysis: dict[str, Any] | None = Field(default=None, description="The AI troubleshooter's report")
    confidence_label: str = ""
    category_label: str = ""
    analysis_error: str
    analysed_at: Any = None
    ticket_id: int | None = None
    summary: dict[str, Any] = Field(description="What the tray app decoded from the dump")


class DumpList(BaseModel):
    dumps: list[Dump]


class AnalyseRequest(BaseModel):
    ticket_id: int | None = Field(
        default=None, gt=0, description="Also post the result to this ticket; it must be linked to the asset",
    )


class AnalyseResult(BaseModel):
    id: int
    analysis_status: str
    ticket_id: int | None = None


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="The report is too large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="The report is too large")
    return bytes(body)


@router.post("/api/tray/minidumps", response_model=UploadResult,
             summary="Blue screen dump summaries from a tray device")
async def upload_minidumps(request: Request, device: dict = Depends(get_current_tray_device)):
    """Store the dumps a tray device decoded, on the device's own asset.

    The body is ``{"dumps": [...]}`` with at most 20 summaries and 2 MB in
    total. A device may report six times in ten minutes.
    """

    body = await _read_body(request)
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The report is not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("dumps"), list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The report needs a list of dumps")
    if not allow_upload(int(device.get("id") or 0), time.monotonic()):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many reports; try again later")
    counts = await service.ingest(device, payload["dumps"])
    return JSONResponse({"status": "ok", **counts})


async def _asset_for(request: Request, asset_id: int, *, write: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(user, asset)`` when the viewer may use RMM on the asset's company; 404 for any other asset."""

    user, company_id, _can_run, redirect = await _rmm_access(request, asset_id=asset_id, write=write)
    if redirect or not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue")
    asset = await asset_repo.get_asset_by_id(asset_id)
    if not asset or int(asset.get("company_id") or 0) != company_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
    return user, dict(asset)


async def _start(request: Request, asset_id: int, dump_id: int, ticket_id: int | None) -> dict[str, Any]:
    user, _asset = await _asset_for(request, asset_id, write=True)
    dump = await repo.get_dump(dump_id)
    if not dump or int(dump.get("asset_id") or 0) != asset_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dump not found.")
    result = await service.start_analysis(dump=dump, requested_by=user, ticket_id=ticket_id)
    await audit_service.log_action(
        action="minidump.analyse", user_id=user.get("id"), entity_type="asset_minidump", entity_id=dump_id,
        new_value={"asset_id": asset_id, "ticket_id": ticket_id}, request=request,
    )
    return result


@router.get("/api/assets/{asset_id}/minidumps", response_model=DumpList,
            summary="An asset's blue screen dumps and their analyses")
async def list_minidumps(asset_id: int, request: Request):
    """The dumps the asset's tray app reported, newest crash first."""

    await _asset_for(request, asset_id, write=False)
    return JSONResponse({"dumps": jsonable_encoder(await service.list_for_asset(asset_id))})


@router.post("/api/assets/{asset_id}/minidumps/{dump_id}/analyse", response_model=AnalyseResult,
             status_code=status.HTTP_202_ACCEPTED, summary="Analyse a blue screen dump with the AI troubleshooter")
async def analyse_minidump(asset_id: int, dump_id: int, body: AnalyseRequest, request: Request):
    """Start the analysis in the background; poll the list for the result.

    409 when the dump is already being analysed, cannot be decoded or no AI
    model is configured; 400 when the ticket is not linked to the asset.
    """

    try:
        result = await _start(request, asset_id, dump_id, body.ticket_id)
    except service.MinidumpError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return JSONResponse(result, status_code=status.HTTP_202_ACCEPTED)


@router.post("/rmm/assets/{asset_id}/minidumps/{dump_id}/analyse", response_class=HTMLResponse,
             summary="Analyse a blue screen dump from the asset page (RMM)")
async def analyse_from_asset(asset_id: int, dump_id: int, request: Request):
    """The asset page's Minidumps section: start the analysis and return to it."""

    target = f"/assets/{asset_id}#minidumps"
    form = await request.form()
    raw_ticket = str(form.get("ticket_id") or "").strip()
    if raw_ticket and not raw_ticket.isdigit():
        await _asset_for(request, asset_id, write=True)
        return flash_redirect(target, "Choose a ticket linked to this asset.", "error")
    try:
        await _start(request, asset_id, dump_id, int(raw_ticket) if raw_ticket else None)
    except service.MinidumpError as exc:
        return flash_redirect(target, str(exc), "error")
    return flash_redirect(
        target, "Analysis started. The likely cause appears here when the AI troubleshooter finishes.", "success",
    )


async def asset_context(asset: dict[str, Any], *, can_run: bool) -> dict[str, Any] | None:
    """The asset page's Minidumps section, or ``None`` when it does not apply.

    Shown for a Windows computer with the tray app, and for any asset that
    already has reported dumps. ``can_run`` is the user's RMM write access.
    """

    asset_id = int(asset["id"])
    device = await health_repo.find_tray_device_for_asset(asset_id)
    windows = bool(device) and ai_troubleshooter.endpoint_platform(device, asset) == "windows"
    dumps = await service.list_for_asset(asset_id)
    if not windows and not dumps:
        return None
    tickets = [
        {"id": ticket["id"], "subject": str(ticket.get("subject") or "Ticket")}
        for ticket in await asset_repo.list_tickets_for_asset(asset_id)
        if str(ticket.get("status") or "").strip().lower() not in _CLOSED_TICKET_STATUSES
    ] if can_run else []
    return {
        "dumps": dumps,
        "count": len(dumps),
        "can_run": bool(can_run),
        "llm_ready": bool(tray_service.build_troubleshoot_llm_config(None).get("model")),
        "tickets": tickets,
        "analysing": any(dump["analysis_status"] == repo.STATUS_ANALYSING for dump in dumps),
    }
