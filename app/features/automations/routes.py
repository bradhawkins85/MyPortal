"""Automations page routes for the ``automations`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Automations"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/admin/automations", response_class=HTMLResponse)
async def admin_automations_page(
    request: Request,
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    main_module = _main()
    return await main_module.admin_automations_page(
        request=request,
        status=status,
        kind=kind,
        success=success,
        error=error,
    )


@router.get("/admin/automations/create/scheduled", response_class=HTMLResponse)
async def admin_create_scheduled_automation_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    main_module = _main()
    return await main_module.admin_create_scheduled_automation_page(
        request=request,
        success=success,
        error=error,
    )


@router.get("/admin/automations/create/event", response_class=HTMLResponse)
async def admin_create_event_automation_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    main_module = _main()
    return await main_module.admin_create_event_automation_page(
        request=request,
        success=success,
        error=error,
    )


@router.post("/admin/automations", response_class=HTMLResponse)
async def admin_create_automation(request: Request):
    return await _main().admin_create_automation(request=request)


@router.get("/admin/automations/{automation_id}/edit", response_class=HTMLResponse)
async def admin_edit_automation_page(automation_id: int, request: Request):
    return await _main().admin_edit_automation_page(automation_id=automation_id, request=request)


@router.post("/admin/automations/{automation_id}", response_class=HTMLResponse)
async def admin_update_automation(automation_id: int, request: Request):
    return await _main().admin_update_automation(automation_id=automation_id, request=request)


@router.post("/admin/automations/{automation_id}/status", response_class=HTMLResponse)
async def admin_update_automation_status(automation_id: int, request: Request):
    return await _main().admin_update_automation_status(automation_id=automation_id, request=request)


@router.post("/admin/automations/{automation_id}/execute", response_class=HTMLResponse)
async def admin_execute_automation(automation_id: int, request: Request):
    return await _main().admin_execute_automation(automation_id=automation_id, request=request)


@router.post("/admin/automations/{automation_id}/delete", response_class=HTMLResponse)
async def admin_delete_automation(automation_id: int, request: Request):
    return await _main().admin_delete_automation(automation_id=automation_id, request=request)


__all__ = ["router"]
