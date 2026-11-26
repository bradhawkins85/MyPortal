"""Fail2ban status page routes for the ``fail2ban`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from . import handlers

router = APIRouter(tags=["Fail2ban"])


router.add_api_route(
    "/admin/fail2ban",
    handlers.admin_fail2ban_page,
    methods=["HEAD", "GET"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
