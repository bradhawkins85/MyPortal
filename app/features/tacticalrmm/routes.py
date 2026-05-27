"""TacticalRMM routes for the ``tacticalrmm`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from . import handlers

router = APIRouter(tags=["TacticalRMM"])

router.add_api_route(
    "/admin/modules/tacticalrmm/push-companies",
    handlers.admin_push_companies_to_tactical_rmm,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/modules/tacticalrmm/pull-companies",
    handlers.admin_pull_companies_from_tactical_rmm,
    methods=["POST"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
