"""Backup admin page routes for the ``backups`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Backups"])


def _main():
    from app import main as main_module

    return main_module


@router.head("/admin/backup-jobs", response_class=HTMLResponse)
@router.get("/admin/backup-jobs", response_class=HTMLResponse)
async def admin_backup_jobs_page(request: Request):
    return await _main().admin_backup_jobs_page(request=request)


@router.head("/admin/backup-summary", response_class=HTMLResponse)
@router.get("/admin/backup-summary", response_class=HTMLResponse)
async def admin_backup_summary_page(request: Request):
    return await _main().admin_backup_summary_page(request=request)


@router.post("/admin/backup-jobs", response_class=HTMLResponse)
async def admin_create_backup_job(request: Request):
    return await _main().admin_create_backup_job(request=request)


@router.post("/admin/backup-jobs/{job_id}", response_class=HTMLResponse)
async def admin_update_backup_job(request: Request, job_id: int):
    return await _main().admin_update_backup_job(request=request, job_id=job_id)


@router.post("/admin/backup-jobs/{job_id}/delete", response_class=HTMLResponse)
async def admin_delete_backup_job(request: Request, job_id: int):
    return await _main().admin_delete_backup_job(request=request, job_id=job_id)


@router.post("/admin/backup-jobs/{job_id}/regenerate-token", response_class=HTMLResponse)
async def admin_regenerate_backup_job_token(request: Request, job_id: int):
    return await _main().admin_regenerate_backup_job_token(request=request, job_id=job_id)


__all__ = ["router"]
