"""Windows Update status and installs on each RMM device (:mod:`app.services.rmm_windows_updates`).

Technician side (company scoped, ``menu.rmm_scripts``; write access scans and installs):

* each asset page's Windows updates section (:func:`asset_windows_updates_context`)
* ``POST /rmm/assets/{id}/windows-updates/scan`` ask the device for a new scan
* ``POST /rmm/assets/{id}/windows-updates/install`` install chosen missing updates
* ``POST /rmm/assets/{id}/windows-updates/block`` block chosen missing updates for
  the device, its company or every company (super admins)
* ``POST /rmm/patching/blocks`` block a KB number from Patch management
* ``POST /rmm/patching/blocks/{id}/delete`` unblock (every company's blocks need a super admin)
* ``GET /api/rmm/assets/{id}/windows-updates`` the device's latest status as JSON
* ``GET /api/rmm/windows-updates/blocks`` the blocks a company's technicians see
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_windows_updates as updates_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import rmm_windows_updates
from app.services.rmm_scripts import RunRequestError

from .inventory_routes import _company_agent
from .routes import _context

router = APIRouter(tags=["RMM Windows updates"])

SECTION = "windows-updates"
PATCHING_BLOCKS = "/rmm/patching#blocked-updates"


def _back(asset_id: int) -> str:
    return f"/assets/{asset_id}#{SECTION}"


async def _device_agent(company_id: int, asset_id: int) -> dict[str, Any] | None:
    agent = await rmm_repo.get_asset_agent(asset_id)
    if agent and agent.get("company_id") is not None and int(agent["company_id"]) != int(company_id):
        return None
    return agent


async def _rescan(agent: dict[str, Any] | None, user_id: int | None) -> None:
    """Scan again so the device hides or shows the update without waiting a day."""

    if not rmm_windows_updates.is_windows(agent):
        return
    try:
        await rmm_windows_updates.queue_scan(agent, user_id=user_id)
    except RunRequestError:
        pass


async def asset_windows_updates_context(company_id: int, asset_id: int, *, can_run: bool,
                                        is_super_admin: bool = False) -> dict[str, Any] | None:
    """The asset page's Windows updates section, or None without a Windows RMM agent."""

    agent = await _device_agent(company_id, asset_id)
    return await rmm_windows_updates.asset_context(agent, can_run=can_run, is_super_admin=is_super_admin)


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


@router.post("/rmm/assets/{asset_id:int}/windows-updates/block", summary="Block chosen Windows updates")
async def block_windows_updates(asset_id: int, request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_run = context
    agent = await _company_agent(company_id, asset_id)
    form = await request.form()
    update_ids = [str(value) for value in form.getlist("update_ids")]
    scope = form.get("block_scope") or "device"
    try:
        added = await rmm_windows_updates.block_updates(
            agent, update_ids, scope=scope, reason=form.get("block_reason"), user_id=user.get("id"),
            is_super_admin=bool(user.get("is_super_admin")),
        )
    except RunRequestError as exc:
        return flash_redirect(_back(asset_id), " ".join(exc.errors.values()), "error")
    await audit_service.record(
        action="rmm.windows_updates.block", request=request, user_id=user.get("id"), entity_type="asset",
        entity_id=asset_id, metadata={"update_ids": update_ids[:rmm_windows_updates.MAX_SELECTED], "scope": scope,
                                      "added": added},
    )
    await _rescan(agent, user.get("id"))
    where = {"device": "this device", "company": "this company", "global": "every company"}[scope]
    return flash_redirect(
        _back(asset_id),
        f"Blocked for {where}. MyPortal will not install {'them' if len(update_ids) != 1 else 'it'}, and devices "
        "hide blocked updates from Windows Update at their next scan.",
        "success",
    )


@router.post("/rmm/patching/blocks", summary="Block a Windows update by KB number")
async def block_kb(request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, _can_run = context
    form = await request.form()
    scope = form.get("scope") or "company"
    try:
        block_id = await rmm_windows_updates.block_kb(
            company_id, form.get("kb"), scope=scope, reason=form.get("reason"), user_id=user.get("id"),
            is_super_admin=bool(user.get("is_super_admin")),
        )
    except RunRequestError as exc:
        return flash_redirect(PATCHING_BLOCKS, " ".join(exc.errors.values()), "error")
    if block_id is None:
        return flash_redirect(PATCHING_BLOCKS, "That update is already blocked there.", "info")
    await audit_service.record(
        action="rmm.windows_updates.block", request=request, user_id=user.get("id"),
        entity_type="rmm_windows_update_block", entity_id=block_id, metadata={"kb": form.get("kb"), "scope": scope},
    )
    return flash_redirect(PATCHING_BLOCKS, "Update blocked. Devices hide it from Windows Update at their next scan.",
                          "success")


@router.post("/rmm/patching/blocks/{block_id:int}/delete", summary="Unblock a Windows update")
async def unblock(block_id: int, request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _company, company_id, can_run = context
    form = await request.form()
    asset_id = str(form.get("asset_id") or "")
    back = _back(int(asset_id)) if asset_id.isdigit() else PATCHING_BLOCKS
    try:
        rule = await rmm_windows_updates.editable_block(
            block_id, company_id, can_edit=can_run, is_super_admin=bool(user.get("is_super_admin")),
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Block not found") from None
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from None
    await updates_repo.delete_block(block_id)
    await audit_service.record(
        action="rmm.windows_updates.unblock", request=request, user_id=user.get("id"),
        entity_type="rmm_windows_update_block", entity_id=block_id,
        before={key: rule.get(key) for key in ("company_id", "asset_id", "kb", "update_id", "title", "reason")},
    )
    device = rule.get("asset_id") or (int(asset_id) if asset_id.isdigit() else None)
    if device is not None:
        await _rescan(await _device_agent(company_id, int(device)), user.get("id"))
    return flash_redirect(back, "Update unblocked. Devices show it again in Windows Update at their next scan.",
                          "success")


@router.get("/api/rmm/windows-updates/blocks", summary="Blocked Windows updates a company's technicians see")
async def blocks_api(request: Request):
    user, _company, company_id, can_run = await _context(request, api=True)
    return {"blocks": [
        rmm_windows_updates.describe_block(rule, can_edit=can_run, is_super_admin=bool(user.get("is_super_admin")))
        for rule in await updates_repo.list_company_blocks(company_id)
    ]}


@router.get("/api/rmm/assets/{asset_id:int}/windows-updates", summary="A device's latest Windows Update status")
async def windows_updates_api(asset_id: int, request: Request):
    user, _company, company_id, can_run = await _context(request, api=True)
    agent = await _company_agent(company_id, asset_id)
    context = await rmm_windows_updates.asset_context(agent, can_run=can_run,
                                                      is_super_admin=bool(user.get("is_super_admin")))
    if context is None:
        return {"supported": False, "scan": None, "missing": [], "blocked": [], "installed": [], "history": []}
    return {
        "supported": True,
        **{key: context[key] for key in ("scan", "missing", "blocked", "block_rules", "installed", "history", "counts",
                                         "scan_pending", "install_pending", "can_run")},
    }
