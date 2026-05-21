"""Continuity admin page routes for the ``continuity`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Continuity"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/admin/business-continuity-plans", response_class=HTMLResponse)
async def admin_business_continuity_plans_page(request: Request):
    return await _main().admin_business_continuity_plans_page(request)


@router.get("/admin/business-continuity-plans/new", response_class=HTMLResponse)
async def admin_new_business_continuity_plan_page(request: Request):
    return await _main().admin_new_business_continuity_plan_page(request)


@router.get("/admin/business-continuity-plans/{plan_id}", response_class=HTMLResponse)
async def admin_edit_business_continuity_plan_page(request: Request, plan_id: int):
    return await _main().admin_edit_business_continuity_plan_page(request, plan_id)


__all__ = ["router"]
