"""Pushing the RMM agent to devices (:mod:`app.services.rmm_agent_deploy`).

Technician side (``menu.rmm_scripts`` with write access, current company):

* ``POST /api/rmm/assets/{id}/agent-install`` push the agent to a device
  through its tray app (Assets list and asset page)
* ``GET /api/rmm/assets/{id}/agent-installs`` every attempt to install the
  agent on a device and how it went (any ``menu.rmm_scripts`` access)

Tray side (the tray device's ``Authorization: Bearer`` token):

* ``GET /api/rmm/agent/package`` the agent build for ``?os=&arch=``: version,
  SHA-256 and size
* ``GET /api/rmm/agent/package/download`` the build itself
* ``POST /api/rmm/agent/install-result`` how an ``install_rmm_agent`` command went

Companies switch on automatic pushes on their tray settings page
(``/admin/companies/{id}/tray``).
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_tray_device
from app.repositories import assets as assets_repo
from app.services import audit as audit_service
from app.services import rmm_agent_deploy as deploy

from .routes import _context

router = APIRouter(tags=["RMM agent deployment"])

_PUSH_MESSAGES = {
    "installed": "The RMM agent is already installed on this device.",
    "sent": "Sent to the device. Its tray app is installing the RMM agent now.",
    "queued": "Queued. The device's tray app installs the RMM agent when it next connects.",
    "updating_tray": (
        "The device's tray app is too old to install the RMM agent, so it was asked to update itself first. "
        "The install starts once the updated tray app reconnects."
    ),
}


@router.post("/api/rmm/assets/{asset_id:int}/agent-install", summary="Push the RMM agent to a device")
async def push_agent(asset_id: int, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    asset = await assets_repo.get_asset_by_id(asset_id)
    if not asset or asset.get("company_id") is None or int(asset["company_id"]) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    try:
        result = await deploy.push_to_asset(asset_id, company_id=company_id, user_id=user.get("id"))
    except deploy.AgentDeployError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if result["command_id"]:
        await audit_service.record(
            action="rmm.agent.push",
            request=request,
            user_id=user.get("id"),
            entity_type="asset",
            entity_id=asset_id,
            metadata={"company_id": company_id, "command_id": result["command_id"], "status": result["status"]},
        )
    return {**result, "message": _PUSH_MESSAGES[result["status"]]}


@router.get("/api/rmm/assets/{asset_id:int}/agent-installs", summary="List a device's RMM agent install attempts")
async def list_agent_installs(asset_id: int, request: Request, limit: int = Query(default=20, ge=1, le=100)):
    _user, _company, company_id, _can_run = await _context(request, api=True)
    asset = await assets_repo.get_asset_by_id(asset_id)
    if not asset or asset.get("company_id") is None or int(asset["company_id"]) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    attempts = await deploy.asset_install_history(asset_id, limit=limit)
    return {"attempts": jsonable_encoder(attempts)}


def _platform(os_name: str, arch: str) -> tuple[str, str]:
    wanted = (os_name.strip().lower(), arch.strip().lower())
    for platform in deploy.PLATFORMS:
        if platform == wanted:
            return platform
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported platform")


async def _package(os_name: str, arch: str, version: str | None = None) -> dict:
    if version is not None:
        try:
            version = deploy.release_version(version)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    package = await deploy.ensure_package(*_platform(os_name, arch), version=version)
    if not package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No RMM agent build for this platform yet")
    return package


@router.get("/api/rmm/agent/package", summary="The RMM agent build for a tray device's platform")
async def agent_package(
    os: str = Query(max_length=16),
    arch: str = Query(max_length=16),
    tray_device: dict = Depends(get_current_tray_device),
    version: str | None = Query(default=None, max_length=128),
):
    os_name, arch_name = _platform(os, arch)
    package = await _package(os_name, arch_name, version)
    query = {"os": os_name, "arch": arch_name}
    if version is not None:
        query["version"] = deploy.release_version(version)
    return {
        "file_name": package["file_name"],
        "version": package["version"],
        "sha256": package["sha256"],
        "size": package["size"],
        "download_path": "/api/rmm/agent/package/download?" + urlencode(query),
    }


@router.get("/api/rmm/agent/package/download", summary="Download the RMM agent build")
async def agent_package_download(
    os: str = Query(max_length=16),
    arch: str = Query(max_length=16),
    tray_device: dict = Depends(get_current_tray_device),
    version: str | None = Query(default=None, max_length=128),
):
    package = await _package(os, arch, version)
    return FileResponse(package["path"], media_type="application/octet-stream", filename=package["file_name"])


class InstallResult(BaseModel):
    command_id: int = Field(ge=1)
    ok: bool
    message: str | None = Field(default=None, max_length=8000)


@router.post("/api/rmm/agent/install-result", summary="Report how an RMM agent install went")
async def agent_install_result(payload: InstallResult, tray_device: dict = Depends(get_current_tray_device)):
    if not await deploy.record_result(tray_device, payload.command_id, ok=payload.ok, message=payload.message):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command not found")
    return {"ok": True}
