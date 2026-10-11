"""Pushing the RMM agent to devices (:mod:`app.services.rmm_agent_deploy`).

Technician side (``menu.rmm_scripts`` with write access, current company):

* ``POST /api/rmm/assets/{id}/agent-install`` push the agent to a device
  through its tray app (Assets list and asset page)

Tray side (the tray device's ``Authorization: Bearer`` token):

* ``GET /api/rmm/agent/package`` the agent build for ``?os=&arch=``: version,
  SHA-256 and size
* ``GET /api/rmm/agent/package/download`` the build itself
* ``POST /api/rmm/agent/install-result`` how an ``install_rmm_agent`` command went

Companies switch on automatic pushes on their tray settings page
(``/admin/companies/{id}/tray``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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


def _platform(os_name: str, arch: str) -> tuple[str, str]:
    wanted = (os_name.strip().lower(), arch.strip().lower())
    for platform in deploy.PLATFORMS:
        if platform == wanted:
            return platform
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported platform")


async def _package(os_name: str, arch: str) -> dict:
    package = await deploy.ensure_package(*_platform(os_name, arch))
    if not package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No RMM agent build for this platform yet")
    return package


@router.get("/api/rmm/agent/package", summary="The RMM agent build for a tray device's platform")
async def agent_package(
    os: str = Query(max_length=16),
    arch: str = Query(max_length=16),
    tray_device: dict = Depends(get_current_tray_device),
):
    os_name, arch_name = _platform(os, arch)
    package = await _package(os_name, arch_name)
    return {
        "file_name": package["file_name"],
        "version": package["version"],
        "sha256": package["sha256"],
        "size": package["size"],
        "download_path": f"/api/rmm/agent/package/download?os={os_name}&arch={arch_name}",
    }


@router.get("/api/rmm/agent/package/download", summary="Download the RMM agent build")
async def agent_package_download(
    os: str = Query(max_length=16),
    arch: str = Query(max_length=16),
    tray_device: dict = Depends(get_current_tray_device),
):
    package = await _package(os, arch)
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
