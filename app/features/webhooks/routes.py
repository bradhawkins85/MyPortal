"""Webhook admin routes for the ``webhooks`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Webhooks"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/admin/webhooks", response_class=HTMLResponse)
async def admin_webhooks(request: Request):
    return await _main().admin_webhooks(request=request)


__all__ = ["router"]
