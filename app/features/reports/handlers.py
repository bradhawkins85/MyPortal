"""Report handlers for the ``reports`` feature pack."""

from __future__ import annotations

from fastapi import File, Request, UploadFile


def _main():
    from app import main as main_module

    return main_module


async def company_overview_report_page(request: Request):
    return await _main().company_overview_report_page(request=request)


async def company_overview_report_pdf(request: Request):
    return await _main().company_overview_report_pdf(request=request)


async def company_overview_report_settings_page(request: Request):
    return await _main().company_overview_report_settings_page(request=request)


async def company_overview_report_settings_save(request: Request):
    return await _main().company_overview_report_settings_save(request=request)


async def admin_report_cover_image_page(request: Request):
    return await _main().admin_report_cover_image_page(request=request)


async def admin_report_cover_image_upload(request: Request, image: UploadFile = File(None)):
    return await _main().admin_report_cover_image_upload(request=request, image=image)


async def admin_report_cover_image_delete(request: Request):
    return await _main().admin_report_cover_image_delete(request=request)


async def admin_report_cover_image_preview(request: Request):
    return await _main().admin_report_cover_image_preview(request=request)

