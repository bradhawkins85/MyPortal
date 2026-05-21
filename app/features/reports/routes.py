"""Report page routes for the ``reports`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse


router = APIRouter(tags=["Reports"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/reports/company-overview", response_class=HTMLResponse)
async def company_overview_report_page(request: Request):
    return await _main().company_overview_report_page(request=request)


@router.get("/reports/company-overview.pdf")
async def company_overview_report_pdf(request: Request):
    return await _main().company_overview_report_pdf(request=request)


@router.get("/reports/company-overview/settings", response_class=HTMLResponse)
async def company_overview_report_settings_page(request: Request):
    return await _main().company_overview_report_settings_page(request=request)


@router.post("/reports/company-overview/settings")
async def company_overview_report_settings_save(request: Request):
    return await _main().company_overview_report_settings_save(request=request)


@router.get("/admin/reports/pdf-cover-image", response_class=HTMLResponse)
async def admin_report_cover_image_page(request: Request):
    return await _main().admin_report_cover_image_page(request=request)


@router.post("/admin/reports/pdf-cover-image", response_class=HTMLResponse)
async def admin_report_cover_image_upload(request: Request, image: UploadFile = File(None)):
    return await _main().admin_report_cover_image_upload(request=request, image=image)


@router.post("/admin/reports/pdf-cover-image/delete", response_class=HTMLResponse)
async def admin_report_cover_image_delete(request: Request):
    return await _main().admin_report_cover_image_delete(request=request)


@router.get(
    "/admin/reports/pdf-cover-image/preview",
    response_class=FileResponse,
    include_in_schema=False,
)
async def admin_report_cover_image_preview(request: Request):
    return await _main().admin_report_cover_image_preview(request=request)


__all__ = ["router"]
