"""Backup admin handlers for the ``backups`` feature pack."""

from __future__ import annotations

from fastapi import Request


def _main():
    from app import main as main_module

    return main_module


async def admin_backup_jobs_page(request: Request):
    return await _main().admin_backup_jobs_page(request=request)


async def admin_backup_summary_page(request: Request):
    return await _main().admin_backup_summary_page(request=request)


async def admin_create_backup_job(request: Request):
    return await _main().admin_create_backup_job(request=request)


async def admin_update_backup_job(request: Request, job_id: int):
    return await _main().admin_update_backup_job(request=request, job_id=job_id)


async def admin_delete_backup_job(request: Request, job_id: int):
    return await _main().admin_delete_backup_job(request=request, job_id=job_id)


async def admin_regenerate_backup_job_token(request: Request, job_id: int):
    return await _main().admin_regenerate_backup_job_token(request=request, job_id=job_id)

