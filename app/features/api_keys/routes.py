"""API keys page routes for the ``api_keys`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["API Keys"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/admin/api-keys", response_class=HTMLResponse)
async def admin_api_keys_page(request: Request):
    return await _main().admin_api_keys_page(request=request)


@router.post("/admin/api-keys", response_class=HTMLResponse)
async def admin_create_api_key_page(request: Request):
    return await _main().admin_create_api_key_page(request=request)


@router.post("/admin/api-keys/update", response_class=HTMLResponse)
async def admin_update_api_key_page(request: Request):
    return await _main().admin_update_api_key_page(request=request)


@router.post("/admin/api-keys/rotate", response_class=HTMLResponse)
async def admin_rotate_api_key_page(request: Request):
    return await _main().admin_rotate_api_key_page(request=request)


@router.post("/admin/api-keys/delete", response_class=HTMLResponse)
async def admin_delete_api_key_page(request: Request):
    return await _main().admin_delete_api_key_page(request=request)


__all__ = ["router"]
