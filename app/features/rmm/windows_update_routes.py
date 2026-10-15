"""Windows Update status and installs on each RMM device (:mod:`app.services.rmm_windows_updates`).

Technician side (company scoped, ``menu.rmm_scripts``; write access scans and installs):

* each asset page's Windows updates section (:func:`asset_windows_updates_context`)
* ``POST /rmm/assets/{id}/windows-updates/scan`` ask the device for a new scan
* ``POST /rmm/assets/{id}/windows-updates/install`` install chosen missing updates
* ``GET /api/rmm/assets/{id}/windows-updates`` the device's latest status as JSON
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.repositories import rmm as rmm_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import rmm_windows_updates
from app.services.rmm_scripts import RunRequestError

from .inventory_routes import _company_agent
from .routes import _context

router = APIRouter(tags=["RMM Windows updates"])

SECTION = "windows-updates"


def _back(asset_id: int) -> str:
    return f"/assets/{asset_id}#{SECTION}"


async def asset_windows_updates_context(company_id: int, asset_id: int, *, can_run: bool) -> dict[str, Any] | None:
    """The asset page's Windows updates section, or None without a Windows RMM agent."""

    agent = await rmm_repo.get_asset_agent(asset_id)
    if agent and agent.get("company_id") is not None and int(agent["company_id"]) != int(company_id):
        agent = None
    return await rmm_windows_updates.asset_context(agent, can_run=can_run)


@router.post("/rmm/assets/{asset_id:int}/windows-updates/scan", summary="Ask a device for a new Windows Update scan")
async def scan_windows_updates(asset_id: int, request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_run = context
    agent = await _company_agent(company_id, asset_id)
    try:
        run_id = await rmm_windows_updates.queue_scan(agent, user_id=user.get("id"))
    except RunRequestError as exc:
        return flash_redirect(_back(asset_id), " ".join(exc.errors.values()), "error")
    if run_id is None:
        return flash_redirect(_back(asset_id), "A Windows Update scan is already waiting for this device.", "info")
    await audit_service.record(
        action="rmm.windows_updates.scan", request=request, user_id=user.get("id"), entity_type="asset",
        entity_id=asset_id, metadata={"run_id": run_id},
    )
    return flash_redirect(
        _back(asset_id),
        "Scan queued. The device checks its update service the next time it collects work, which can take a few minutes.",
        "success",
    )


@router.post("/rmm/assets/{asset_id:int}/windows-updates/install", summary="Install chosen Windows updates on a device")
async def install_windows_updates(asset_id: int, request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_run = context
    agent = await _company_agent(company_id, asset_id)
    form = await request.form()
    update_ids = [str(value) for value in form.getlist("update_ids")]
    try:
        run_id = await rmm_windows_updates.queue_install(
            agent, update_ids, reboot_mode=form.get("reboot_mode") or "never",
            reboot_delay_minutes=form.get("reboot_delay_minutes"), user_id=user.get("id"),
        )
    except RunRequestError as exc:
        return flash_redirect(_back(asset_id), " ".join(exc.errors.values()), "error")
    await audit_service.record(
        action="rmm.windows_updates.install", request=request, user_id=user.get("id"), entity_type="asset",
        entity_id=asset_id,
        metadata={"run_id": run_id, "update_ids": update_ids[:rmm_windows_updates.MAX_SELECTED],
                  "reboot_mode": form.get("reboot_mode") or "never"},
    )
    count = len({value.strip().lower() for value in update_ids if value.strip()})
    return flash_redirect(
        _back(asset_id),
        f"Installation of {count} update{'s' if count != 1 else ''} queued. The list refreshes when the device "
        "reports back; results appear in Scripts run history.",
        "success",
    )


@router.get("/api/rmm/assets/{asset_id:int}/windows-updates", summary="A device's latest Windows Update status")
async def windows_updates_api(asset_id: int, request: Request):
    _user, _company, company_id, can_run = await _context(request, api=True)
    agent = await _company_agent(company_id, asset_id)
    context = await rmm_windows_updates.asset_context(agent, can_run=can_run)
    if context is None:
        return {"supported": False, "scan": None, "missing": [], "installed": [], "history": []}
    return {
        "supported": True,
        **{key: context[key] for key in ("scan", "missing", "installed", "history", "counts", "scan_pending",
                                         "install_pending", "can_run")},
    }
