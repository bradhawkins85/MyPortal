"""Compliance routes for the ``compliance`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Compliance"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/compliance", response_class=HTMLResponse)
async def compliance_page(request: Request):
    return await _main().compliance_page(request)


@router.get("/compliance/control/{control_id}", response_class=HTMLResponse)
async def compliance_control_requirements_page(request: Request, control_id: int):
    return await _main().compliance_control_requirements_page(request, control_id)


@router.get("/compliance-checks", response_class=HTMLResponse)
async def compliance_checks_page(request: Request):
    return await _main().compliance_checks_page(request)


@router.get("/compliance-checks/{assignment_id}", response_class=HTMLResponse)
async def compliance_checks_detail_page(request: Request, assignment_id: int):
    return await _main().compliance_checks_detail_page(request, assignment_id)


@router.get("/admin/compliance-checks/library", response_class=HTMLResponse)
async def compliance_checks_library_page(request: Request):
    return await _main().compliance_checks_library_page(request)


__all__ = ["router"]
