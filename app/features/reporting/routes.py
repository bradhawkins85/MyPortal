"""Reporting routes for the ``reporting`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Reporting"])


def _main():
    from app import main as main_module

    return main_module


def _add(path: str, endpoint_name: str, methods: list[str], **kwargs) -> None:
    router.add_api_route(path, getattr(_main(), endpoint_name), methods=methods, **kwargs)


_add("/reporting", "reporting_page", ["GET"], response_class=HTMLResponse)
_add("/reporting/{report_id}/export", "reporting_export", ["GET"])
_add("/admin/reporting", "admin_reporting", ["GET"], response_class=HTMLResponse)
_add("/admin/reporting/new", "admin_reporting_new", ["GET"], response_class=HTMLResponse)
_add(
    "/admin/reporting/{report_id}/edit",
    "admin_reporting_edit",
    ["GET"],
    response_class=HTMLResponse,
)
_add("/admin/reporting", "admin_reporting_create", ["POST"], response_class=HTMLResponse)
_add(
    "/admin/reporting/{report_id}",
    "admin_reporting_update",
    ["POST"],
    response_class=HTMLResponse,
)
_add(
    "/admin/reporting/{report_id}/delete",
    "admin_reporting_delete",
    ["POST"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
