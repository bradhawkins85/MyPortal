"""Syncro routes for the ``syncro`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Request


router = APIRouter(tags=["Syncro"])


def _main():
    from app import main as main_module

    return main_module


@router.post("/admin/syncro/import-contacts")
async def route_import_syncro_contacts(request: Request):
    return await _main().import_syncro_contacts(request=request)


@router.post("/admin/syncro/import-companies")
async def route_import_syncro_companies(request: Request):
    return await _main().import_syncro_companies(request=request)


@router.post("/admin/syncro/import-tickets")
async def route_import_syncro_tickets(request: Request):
    return await _main().import_syncro_tickets(request=request)


__all__ = ["router"]
