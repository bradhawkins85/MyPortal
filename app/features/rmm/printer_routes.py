"""Installed printers on each RMM device (:mod:`app.services.rmm_printers`).

Technician side (company scoped, ``menu.rmm_scripts``; write access scans):

* each asset page's Printers section (:func:`asset_printers_context`)
* ``POST /rmm/assets/{id}/printers/scan`` ask the device for a new printer scan
* ``GET /api/rmm/assets/{id}/printers`` the device's printers as JSON

Scans run as built-in scripts through the existing RMM agent, so no agent
update is needed.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import RedirectResponse
from fastapi import APIRouter, Request

from app.repositories import rmm as rmm_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import rmm_printers
from app.services.rmm_scripts import RunRequestError

from .inventory_routes import _company_agent
from .routes import _context

router = APIRouter(tags=["RMM printers"])

SECTION = "printers"


def _back(asset_id: int) -> str:
    return f"/assets/{asset_id}#{SECTION}"


async def asset_printers_context(company_id: int, asset_id: int, *, can_run: bool) -> dict[str, Any] | None:
    """The asset page's Printers section, or None without a Windows RMM agent."""

    agent = await rmm_repo.get_asset_agent(asset_id)
    if agent and agent.get("company_id") is not None and int(agent["company_id"]) != int(company_id):
        agent = None
    return await rmm_printers.asset_context(agent, can_run=can_run)


@router.post("/rmm/assets/{asset_id:int}/printers/scan", summary="Ask a device for a new printer scan")
async def scan_printers(asset_id: int, request: Request):
    context = await _context(request, write=True, asset_id=asset_id)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_run = context
    agent = await _company_agent(company_id, asset_id)
    try:
        run_id = await rmm_printers.queue_scan(agent, user_id=user.get("id"))
    except RunRequestError as exc:
        return flash_redirect(_back(asset_id), " ".join(exc.errors.values()), "error")
    if run_id is None:
        return flash_redirect(_back(asset_id), "A printer scan is already waiting for this device.", "info")
    await audit_service.record(
        action="rmm.printers.scan", request=request, user_id=user.get("id"), entity_type="asset",
        entity_id=asset_id, metadata={"run_id": run_id},
    )
    return flash_redirect(
        _back(asset_id),
        "Printer scan queued. The list updates the next time the device collects work, usually within a few minutes.",
        "success",
    )


@router.get("/api/rmm/assets/{asset_id:int}/printers", summary="A device's installed printers")
async def printers_api(asset_id: int, request: Request):
    _user, _company, company_id, can_run = await _context(request, api=True, asset_id=asset_id)
    agent = await _company_agent(company_id, asset_id)
    context = await rmm_printers.asset_context(agent, can_run=can_run)
    if context is None:
        return {"supported": False, "scan": None, "printers": []}
    return {"supported": True, **{key: context[key] for key in ("scan", "printers", "counts", "scan_pending", "can_run")}}
