"""Backup admin page routes for the ``backups`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from . import handlers

router = APIRouter(tags=["Backups"])


router.add_api_route(
    "/admin/backup-jobs",
    handlers.admin_backup_jobs_page,
    methods=["HEAD", "GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/backup-summary",
    handlers.admin_backup_summary_page,
    methods=["HEAD", "GET"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/backup-jobs",
    handlers.admin_create_backup_job,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/backup-jobs/{job_id}",
    handlers.admin_update_backup_job,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/backup-jobs/{job_id}/delete",
    handlers.admin_delete_backup_job,
    methods=["POST"],
    response_class=HTMLResponse,
)
router.add_api_route(
    "/admin/backup-jobs/{job_id}/regenerate-token",
    handlers.admin_regenerate_backup_job_token,
    methods=["POST"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
