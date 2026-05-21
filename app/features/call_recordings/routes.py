"""Call recordings page routes for the ``call_recordings`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Call Recordings"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/admin/call-recordings", response_class=HTMLResponse)
async def admin_call_recordings_page(request: Request):
    return await _main().admin_call_recordings_page(request=request)


__all__ = ["router"]
