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
    """Admin page for viewing call recordings and transcriptions (super admin only)."""
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    return await main_module._render_template(
        "admin/call_recordings.html",
        request,
        current_user,
        extra={
            "title": "Call Recordings & Transcriptions",
        },
    )


__all__ = ["router"]
