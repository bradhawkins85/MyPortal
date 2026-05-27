"""Report page routes for the ``reports`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

from . import handlers

router = APIRouter(tags=["Reports"])

router.add_api_route(
    "/reports/company-overview",
    handlers.company_overview_report_page,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/reports/company-overview.pdf",
    handlers.company_overview_report_pdf,
    methods=["GET"],
)
router.add_api_route(
    "/reports/company-overview/settings",
    handlers.company_overview_report_settings_page,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/reports/company-overview/settings",
    handlers.company_overview_report_settings_save,
    methods=["POST"],
)
router.add_api_route(
    "/admin/reports/pdf-cover-image",
    handlers.admin_report_cover_image_page,
    methods=["GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/reports/pdf-cover-image",
    handlers.admin_report_cover_image_upload,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/reports/pdf-cover-image/delete",
    handlers.admin_report_cover_image_delete,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/reports/pdf-cover-image/preview",
    handlers.admin_report_cover_image_preview,
    methods=["GET"],
    response_class=FileResponse,
    include_in_schema=False,
)


__all__ = ["router"]
