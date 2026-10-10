"""Remote control through RustDesk and MeshCentral (:mod:`app.services.rmm_remote_control`).

* ``GET /rmm/remote-control`` and ``POST /rmm/remote-control/{provider}``
  each provider's activation script and connection settings (super admin)
* ``POST /api/rmm/assets/{id}/remote-control`` run the activation script on a
  device (``menu.rmm_scripts`` with write access)
* ``GET /api/rmm/remote-sessions/{id}`` the session's progress and, once the
  script has reported back, its launch link (the technician who started it)
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.repositories import assets as assets_repo
from app.repositories import rmm as rmm_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import rmm_remote_control as remote_control
from app.services import rmm_scripts

from .routes import _assets_routes, _context

router = APIRouter(tags=["RMM remote control"])

SETTINGS_PATH = "/rmm/remote-control"


async def _super_admin_context(request: Request):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    if not context[0].get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    return context


@router.get(SETTINGS_PATH, response_class=HTMLResponse, summary="Remote control settings")
async def settings_page(request: Request):
    context = await _super_admin_context(request)
    if isinstance(context, RedirectResponse):
        return context
    user = context[0]
    # Activation scripts run on every company's devices, so only Common scripts are offered.
    scripts = [script for script in await rmm_repo.list_scripts() if script.get("company_id") is None]
    return await _assets_routes()._main()._render_template(
        "rmm/remote_control.html",
        request,
        user,
        extra={
            "title": "Remote control",
            "providers": await remote_control.load_settings(),
            "scripts": [{"id": script["id"], "name": script["name"], "path": script["path"]} for script in scripts],
            "variables": await rmm_scripts.available_variables(),
        },
    )


@router.post(SETTINGS_PATH + "/{provider}", response_class=HTMLResponse, summary="Save a remote control provider")
async def save_settings(provider: str, request: Request):
    context = await _super_admin_context(request)
    if isinstance(context, RedirectResponse):
        return context
    user = context[0]
    form = await request.form()
    try:
        await remote_control.save_settings(provider, dict(form), user_id=user.get("id"))
    except remote_control.RemoteControlError as exc:
        return flash_redirect(SETTINGS_PATH, str(exc), "error")
    await audit_service.record(
        action="rmm.remote_control.settings",
        request=request,
        user_id=user.get("id"),
        entity_type="rmm_remote_control",
        metadata={"provider": provider, "enabled": str(form.get("is_enabled") or "") != ""},
    )
    return flash_redirect(SETTINGS_PATH, f"{remote_control.PROVIDER_LABELS[provider]} settings saved.", "success")


class StartRequest(BaseModel):
    provider: Literal["rustdesk", "meshcentral"]


@router.post("/api/rmm/assets/{asset_id:int}/remote-control", summary="Switch on remote control for a device")
async def start_session(asset_id: int, payload: StartRequest, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    asset = await assets_repo.get_asset_by_id(asset_id)
    if not asset or int(asset.get("company_id") or 0) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    try:
        session = await remote_control.start(
            provider=payload.provider, company_id=company_id, asset_id=asset_id, user_id=user.get("id")
        )
    except remote_control.RemoteControlError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    await audit_service.record(
        action="rmm.remote_control.start",
        request=request,
        user_id=user.get("id"),
        entity_type="asset",
        entity_id=asset_id,
        metadata={"provider": payload.provider, "session_id": session["id"], "run_id": session.get("run_id")},
    )
    return {"session": session}


@router.get("/api/rmm/remote-sessions/{session_id:int}", summary="Check a remote control session")
async def get_session(session_id: int, request: Request):
    user, _company, company_id, _can_run = await _context(request, write=True, api=True)
    session = await remote_control.session_status(
        session_id,
        company_id=company_id,
        user_id=user.get("id"),
        is_super_admin=bool(user.get("is_super_admin")),
    )
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return JSONResponse({"session": session}, headers={"Cache-Control": "no-store"})
