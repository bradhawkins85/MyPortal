"""Backup admin handlers for the ``backups`` feature pack."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Request, status
from fastapi.responses import RedirectResponse
from starlette.datastructures import FormData


def _main():
    from app import main as main_module

    return main_module


def _backup_status_webhook_url(request: Request) -> str:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.portal_url:
        base = str(settings.portal_url).rstrip("/")
    else:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        base = f"{scheme}://{request.url.netloc}"
    return f"{base}/api/backup-status"


def _extract_backup_job_form(form: FormData) -> dict[str, Any]:
    company_id_raw = (form.get("company_id") or "").strip()
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        company_id = 0

    def _parse_alert_days(key: str) -> int | None:
        raw = (form.get(key) or "").strip()
        if not raw:
            return None
        try:
            val = int(raw)
            return val if val > 0 else None
        except (TypeError, ValueError):
            return None

    return {
        "company_id": company_id,
        "name": (form.get("name") or "").strip(),
        "description": (form.get("description") or "").strip() or None,
        "is_active": form.get("is_active") in {"on", "true", "1", "yes"},
        "pass_protection": form.get("pass_protection") in {"on", "true", "1", "yes"},
        "alert_no_success_days": _parse_alert_days("alert_no_success_days"),
        "alert_fail_days": _parse_alert_days("alert_fail_days"),
        "alert_unknown_days": _parse_alert_days("alert_unknown_days"),
    }


async def admin_backup_jobs_page(request: Request):
    from app.repositories import companies as company_repo
    from app.services import backup_jobs as backup_jobs_service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    company_filter_raw = (request.query_params.get("company_id") or "").strip()
    status_filter = (request.query_params.get("status_filter") or "").strip().lower()
    company_filter: int | None = None
    if company_filter_raw:
        try:
            company_filter = int(company_filter_raw)
        except ValueError:
            company_filter = None

    jobs = await backup_jobs_service.list_jobs_with_latest(
        company_id=company_filter, include_inactive=True
    )
    if status_filter and status_filter in backup_jobs_service.KNOWN_STATUSES:
        jobs = [job for job in jobs if job.get("today_status") == status_filter]

    summary = backup_jobs_service.summarise_jobs(jobs)
    companies = await company_repo.list_companies()
    company_lookup = {
        int(company["id"]): company.get("name")
        for company in companies
        if company.get("id") is not None
    }

    job_id_param = request.query_params.get("jobId")
    editing_job: dict[str, Any] | None = None
    if job_id_param:
        try:
            editing_job = await backup_jobs_service.get_job(int(job_id_param))
        except (TypeError, ValueError):
            editing_job = None

    extra = {
        "title": "Backup history",
        "backup_jobs": jobs,
        "backup_jobs_summary": summary,
        "backup_status_definitions": backup_jobs_service.STATUS_DEFINITIONS,
        "backup_status_default": backup_jobs_service.DEFAULT_STATUS,
        "backup_companies": companies,
        "backup_company_lookup": company_lookup,
        "backup_company_filter": company_filter,
        "backup_status_filter": status_filter,
        "backup_editing_job": editing_job,
        "backup_status_url": _backup_status_webhook_url(request),
    }
    return await _main()._render_template("admin/backup_jobs.html", request, user, extra=extra)


async def admin_backup_summary_page(request: Request):
    from app.repositories import companies as company_repo
    from app.services import backup_jobs as backup_jobs_service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    company_filter_raw = (request.query_params.get("company_id") or "").strip()
    status_filter = (request.query_params.get("status_filter") or "").strip().lower()
    company_filter: int | None = None
    if company_filter_raw:
        try:
            company_filter = int(company_filter_raw)
        except ValueError:
            company_filter = None

    jobs = await backup_jobs_service.list_jobs_with_latest(
        company_id=company_filter, include_inactive=True
    )
    if status_filter and status_filter in backup_jobs_service.KNOWN_STATUSES:
        jobs = [job for job in jobs if job.get("today_status") == status_filter]

    summary = backup_jobs_service.summarise_jobs(jobs)
    companies = await company_repo.list_companies()
    company_lookup = {
        int(company["id"]): company.get("name")
        for company in companies
        if company.get("id") is not None
    }

    history = await backup_jobs_service.build_history_grid(
        company_id=company_filter, days=14, include_inactive=True
    )

    extra = {
        "title": "Backup Summary",
        "backup_jobs": jobs,
        "backup_jobs_summary": summary,
        "backup_status_definitions": backup_jobs_service.STATUS_DEFINITIONS,
        "backup_status_default": backup_jobs_service.DEFAULT_STATUS,
        "backup_companies": companies,
        "backup_company_lookup": company_lookup,
        "backup_company_filter": company_filter,
        "backup_status_filter": status_filter,
        "backup_history": history,
    }
    return await _main()._render_template("admin/backup_summary.html", request, user, extra=extra)


async def admin_create_backup_job(request: Request):
    from app.services import audit as audit_service
    from app.services import backup_jobs as backup_jobs_service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    payload = _extract_backup_job_form(form)
    try:
        job = await backup_jobs_service.create_job(
            company_id=payload["company_id"],
            name=payload["name"],
            description=payload["description"],
            is_active=payload["is_active"],
            created_by=int(user.get("id")) if user.get("id") else None,
            alert_no_success_days=payload["alert_no_success_days"],
            alert_fail_days=payload["alert_fail_days"],
            alert_unknown_days=payload["alert_unknown_days"],
            pass_protection=payload["pass_protection"],
        )
    except ValueError as exc:
        url = f"/admin/backup-jobs?error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    await audit_service.log_action(
        action="backup_job.create",
        user_id=user.get("id"),
        entity_type="backup_job",
        entity_id=job["id"],
        metadata={"company_id": job["company_id"], "name": job["name"]},
        request=request,
    )
    return RedirectResponse(
        url=f"/admin/backup-jobs?success={quote('Backup job created.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_update_backup_job(request: Request, job_id: int):
    from app.services import audit as audit_service
    from app.services import backup_jobs as backup_jobs_service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    payload = _extract_backup_job_form(form)
    try:
        updated = await backup_jobs_service.update_job(
            job_id,
            company_id=payload["company_id"] or None,
            name=payload["name"],
            description=payload["description"],
            is_active=payload["is_active"],
            alert_no_success_days=payload["alert_no_success_days"],
            alert_fail_days=payload["alert_fail_days"],
            alert_unknown_days=payload["alert_unknown_days"],
            clear_alert_no_success_days=payload["alert_no_success_days"] is None,
            clear_alert_fail_days=payload["alert_fail_days"] is None,
            clear_alert_unknown_days=payload["alert_unknown_days"] is None,
            pass_protection=payload["pass_protection"],
        )
    except ValueError as exc:
        url = f"/admin/backup-jobs?jobId={int(job_id)}&error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    if not updated:
        url = f"/admin/backup-jobs?error={quote('Backup job not found.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    await audit_service.log_action(
        action="backup_job.update",
        user_id=user.get("id"),
        entity_type="backup_job",
        entity_id=job_id,
        metadata={
            "company_id": updated["company_id"],
            "name": updated["name"],
            "is_active": updated["is_active"],
        },
        request=request,
    )
    return RedirectResponse(
        url=f"/admin/backup-jobs?success={quote('Backup job updated.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_delete_backup_job(request: Request, job_id: int):
    from app.services import audit as audit_service
    from app.services import backup_jobs as backup_jobs_service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        await backup_jobs_service.delete_job(job_id)
    except Exception as exc:  # pragma: no cover - defensive
        url = f"/admin/backup-jobs?error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    await audit_service.log_action(
        action="backup_job.delete",
        user_id=user.get("id"),
        entity_type="backup_job",
        entity_id=job_id,
        request=request,
    )
    return RedirectResponse(
        url=f"/admin/backup-jobs?success={quote('Backup job deleted.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def admin_regenerate_backup_job_token(request: Request, job_id: int):
    from app.services import audit as audit_service
    from app.services import backup_jobs as backup_jobs_service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    updated = await backup_jobs_service.regenerate_token(job_id)
    if not updated:
        url = f"/admin/backup-jobs?error={quote('Backup job not found.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    await audit_service.log_action(
        action="backup_job.regenerate_token",
        user_id=user.get("id"),
        entity_type="backup_job",
        entity_id=job_id,
        request=request,
    )
    return RedirectResponse(
        url=f"/admin/backup-jobs?jobId={int(job_id)}&success={quote('Token regenerated.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
