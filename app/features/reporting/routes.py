"""Reporting routes for the ``reporting`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from . import handlers

router = APIRouter(tags=["Reporting"])


def _add(path: str, endpoint, methods: list[str], **kwargs) -> None:
    router.add_api_route(path, endpoint, methods=methods, **kwargs)


_add("/reporting", handlers.reporting_page, ["GET"], response_class=HTMLResponse)
_add("/reporting/{report_id}/export", handlers.reporting_export, ["GET"])
_add("/admin/reporting", handlers.admin_reporting, ["GET"], response_class=HTMLResponse)
_add("/admin/reporting/new", handlers.admin_reporting_new, ["GET"], response_class=HTMLResponse)
_add(
    "/admin/reporting/{report_id}/edit",
    handlers.admin_reporting_edit,
    ["GET"],
    response_class=HTMLResponse,
)
_add("/admin/reporting", handlers.admin_reporting_create, ["POST"], response_class=HTMLResponse)
_add(
    "/admin/reporting/{report_id}",
    handlers.admin_reporting_update,
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/reporting/{report_id}/delete",
    handlers.admin_reporting_delete,
    ["POST"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
