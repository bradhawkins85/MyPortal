"""API keys page routes for the ``api_keys`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from . import handlers


router = APIRouter(tags=["API Keys"])

router.add_api_route(
    "/admin/api-keys",
    handlers.admin_api_keys_page,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/api-keys",
    handlers.admin_create_api_key_page,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/api-keys/update",
    handlers.admin_update_api_key_page,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/api-keys/rotate",
    handlers.admin_rotate_api_key_page,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/api-keys/delete",
    handlers.admin_delete_api_key_page,
    methods=["POST"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
