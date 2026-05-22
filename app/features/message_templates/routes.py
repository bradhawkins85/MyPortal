"""Message template page routes for the ``message_templates`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Message Templates"])


def _main():
    from app import main as main_module

    return main_module


def _add(path: str, endpoint_name: str, methods: list[str], **kwargs) -> None:
    router.add_api_route(
        path, getattr(_main(), endpoint_name), methods=methods, **kwargs
    )


_add(
    "/admin/message-templates",
    "admin_message_templates",
    ["GET"],
    response_class=HTMLResponse,
)
_add(
    "/admin/message-templates/new",
    "admin_message_templates_new",
    ["GET"],
    response_class=HTMLResponse,
)
_add(
    "/admin/message-templates/{template_id}/edit",
    "admin_message_templates_edit",
    ["GET"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
