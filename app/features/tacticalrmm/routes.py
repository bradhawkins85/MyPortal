"""TacticalRMM routes for the ``tacticalrmm`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["TacticalRMM"])


def _main():
    from app import main as main_module

    return main_module


@router.post("/admin/modules/tacticalrmm/push-companies", response_class=HTMLResponse)
async def route_admin_push_companies_to_tactical_rmm(request: Request):
    return await _main().admin_push_companies_to_tactical_rmm(request=request)


@router.post("/admin/modules/tacticalrmm/pull-companies", response_class=HTMLResponse)
async def route_admin_pull_companies_from_tactical_rmm(request: Request):
    return await _main().admin_pull_companies_from_tactical_rmm(request=request)


__all__ = ["router"]
