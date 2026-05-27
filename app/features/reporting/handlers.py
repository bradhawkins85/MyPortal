"""Reporting handlers for the ``reporting`` feature pack."""

from __future__ import annotations

from fastapi import Request


def _main():
    from app import main as main_module

    return main_module


async def reporting_page(request: Request):
    return await _main().reporting_page(request=request)


async def reporting_export(report_id: int, request: Request):
    return await _main().reporting_export(report_id=report_id, request=request)


async def admin_reporting(request: Request):
    return await _main().admin_reporting(request=request)


async def admin_reporting_new(request: Request):
    return await _main().admin_reporting_new(request=request)


async def admin_reporting_edit(report_id: int, request: Request):
    return await _main().admin_reporting_edit(report_id=report_id, request=request)


async def admin_reporting_create(request: Request):
    return await _main().admin_reporting_create(request=request)


async def admin_reporting_update(report_id: int, request: Request):
    return await _main().admin_reporting_update(report_id=report_id, request=request)


async def admin_reporting_delete(report_id: int, request: Request):
    return await _main().admin_reporting_delete(report_id=report_id, request=request)


__all__ = [
    "reporting_page",
    "reporting_export",
    "admin_reporting",
    "admin_reporting_new",
    "admin_reporting_edit",
    "admin_reporting_create",
    "admin_reporting_update",
    "admin_reporting_delete",
]
