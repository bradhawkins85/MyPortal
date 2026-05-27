"""Issue tracker admin page routes for the ``issue_tracker`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from . import handlers

router = APIRouter(tags=["Issue Tracker"])


router.add_api_route(
    "/admin/issues",
    handlers.admin_issue_tracker,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/issues",
    handlers.admin_create_issue,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/issues/{issue_id}/update",
    handlers.admin_update_issue,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/issues/{issue_id}/assignments/{assignment_id}/status",
    handlers.admin_update_issue_assignment_status,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/issues/{issue_id}/assignments/{assignment_id}/delete",
    handlers.admin_delete_issue_assignment,
    methods=["POST"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
