"""Assets routes for the ``assets`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse


router = APIRouter(tags=["Assets"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/assets", response_class=HTMLResponse)
async def assets_page(request: Request):
    return await _main().assets_page(request)


@router.get("/assets/settings", response_class=HTMLResponse)
async def assets_settings_page(request: Request):
    return await _main().assets_settings_page(request)


@router.delete("/assets/{asset_id}", response_class=JSONResponse)
async def delete_asset(request: Request, asset_id: int):
    return await _main().delete_asset(request, asset_id)


__all__ = ["router"]
