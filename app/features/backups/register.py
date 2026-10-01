"""Assets & Network > Backups: the company backup register.

Lists the company's status-tracked jobs from ``/admin/backup-jobs`` alongside
manual entries that are not tracked, and documents each one's backup app,
frequency, source, destinations, encryption key links and notes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.repositories import backup_register as repo
from app.security.flash import flash_redirect

router = APIRouter(tags=["Backups"])

PERMISSION = "menu.backups"
CREDENTIAL_PERMISSION = "menu.credentials"


def _main():
    from app import main as main_module

    return main_module


async def _context(request: Request, *, write: bool = False):
    """Return ``(user, membership, company, company_id, can_edit)`` or a redirect."""
    from app.repositories import companies as company_repo

    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return redirect
    try:
        company_id = int(user.get("company_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company") from None
    membership = await main_module._get_effective_company_membership(request, user["id"], company_id)
    if not main_module._membership_menu_can(user, membership, PERMISSION):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    can_edit = main_module._membership_menu_can(user, membership, PERMISSION, write=True)
    if write and not can_edit:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Backups write access required")
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, can_edit


async def _vault_credentials(user: dict, membership: dict | None, company_id: int) -> list[dict[str, Any]] | None:
    """Credential metadata the user may see, or ``None`` when the vault is unavailable to them."""
    from app.repositories import vault as vault_repo

    main_module = _main()
    if not main_module._feature_pack_available("shared_credentials"):
        return None
    if not main_module._membership_menu_can(user, membership, CREDENTIAL_PERMISSION):
        return None
    if not await vault_repo.credential_feature_enabled(company_id):
        return None
    rows = await vault_repo.list_credentials(company_id)
    return [
        {"id": int(row["id"]), "name": row.get("name") or f"Credential #{row['id']}",
         "archived": bool(row.get("archived_at") or row.get("revoked_at"))}
        for row in rows
    ]


async def _tracked_jobs(company_id: int) -> list[dict[str, Any]]:
    from app.services import backup_jobs as backup_jobs_service

    jobs = await backup_jobs_service.list_jobs_with_latest(company_id=company_id, include_inactive=True)
    # The webhook token is a secret for super admins only; never pass it to this page.
    return [{key: value for key, value in job.items() if key != "token"} for job in jobs]


def _status_variant(value: str | None) -> str:
    return {"pass": "success", "warn": "warning", "fail": "danger"}.get(value or "", "neutral")


@router.get("/backups", response_class=HTMLResponse, summary="Company backup register")
async def backups_page(request: Request):
    context = await _context(request)
    if isinstance(context, RedirectResponse):
        return context
    user, membership, company, company_id, can_edit = context
    jobs = await _tracked_jobs(company_id)
    entries = await repo.list_entries(company_id, job_ids=[job["id"] for job in jobs])
    credentials = await _vault_credentials(user, membership, company_id)
    credential_names = {item["id"]: item["name"] for item in credentials or []}

    by_job = {entry["backup_job_id"]: entry for entry in entries if entry.get("backup_job_id")}
    rows: list[dict[str, Any]] = []
    for job in jobs:
        rows.append({
            "kind": "tracked",
            "name": job["name"],
            "description": job.get("description"),
            "is_active": job.get("is_active"),
            "today_status": job.get("today_status"),
            "status_variant": _status_variant(job.get("today_status")),
            "edit_url": f"/backups/tracked/{job['id']}/edit",
            "details": by_job.get(job["id"]),
        })
    for entry in entries:
        if entry.get("backup_job_id"):
            continue
        rows.append({
            "kind": "manual",
            "name": entry["name"],
            "edit_url": f"/backups/{entry['id']}/edit",
            "delete_url": f"/backups/{entry['id']}/delete",
            "details": entry,
        })
    rows.sort(key=lambda row: str(row["name"] or "").casefold())
    status_counts = {"pass": 0, "warn": 0, "fail": 0}
    for job in jobs:
        if job.get("today_status") in status_counts:
            status_counts[job["today_status"]] += 1

    return await _main()._render_template("backups/index.html", request, user, extra={
        "title": "Backups",
        "company": company,
        "can_edit": can_edit,
        "rows": rows,
        "tracked_count": len(jobs),
        "manual_count": len(rows) - len(jobs),
        "status_counts": status_counts,
        "vault_available": credentials is not None,
        "credential_names": credential_names,
    })


async def _render_form(
    request: Request,
    user: dict,
    membership: dict | None,
    company_id: int,
    *,
    entry: dict[str, Any] | None,
    job: dict[str, Any] | None,
    action: str,
):
    credentials = await _vault_credentials(user, membership, company_id)
    title = (f"Backup details: {job['name']}" if job
             else "Edit backup" if entry else "Add backup")
    return await _main()._render_template("backups/form.html", request, user, extra={
        "title": title,
        "entry": entry or {},
        "job": job,
        "form_action": action,
        "delete_url": f"/backups/{entry['id']}/delete" if entry and not job else None,
        "backup_apps": await repo.list_apps(),
        "frequency_suggestions": repo.FREQUENCY_SUGGESTIONS,
        "vault_credentials": credentials,
    })


async def _company_job(company_id: int, job_id: int) -> dict[str, Any]:
    from app.repositories import backup_jobs as backup_jobs_repo

    job = await backup_jobs_repo.get_job(job_id)
    if not job or int(job["company_id"]) != int(company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup job not found")
    return job


@router.get("/backups/new", response_class=HTMLResponse, summary="Add a manual backup entry")
async def new_backup_page(request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, membership, _company, company_id, _can_edit = context
    return await _render_form(request, user, membership, company_id, entry=None, job=None, action="/backups")


@router.get("/backups/{entry_id}/edit", response_class=HTMLResponse, summary="Edit a manual backup entry")
async def edit_backup_page(request: Request, entry_id: int):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, membership, _company, company_id, _can_edit = context
    entry = await repo.get_entry(company_id, entry_id)
    if not entry or entry.get("backup_job_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup not found")
    return await _render_form(request, user, membership, company_id, entry=entry, job=None,
                              action=f"/backups/{entry_id}")


@router.get("/backups/tracked/{job_id}/edit", response_class=HTMLResponse,
            summary="Document a status-tracked backup job")
async def edit_tracked_backup_page(request: Request, job_id: int):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, membership, _company, company_id, _can_edit = context
    job = await _company_job(company_id, job_id)
    entry = await repo.get_entry_for_job(job_id)
    return await _render_form(request, user, membership, company_id, entry=entry, job=job,
                              action=f"/backups/tracked/{job_id}")


async def _clean_submission(
    request: Request,
    user: dict,
    membership: dict | None,
    company_id: int,
    *,
    existing: dict[str, Any] | None,
    require_name: bool,
) -> tuple[dict[str, Any], int | None]:
    form = await request.form()
    values = {key: form.get(key) for key in (
        "name", "backup_app_id", "new_backup_app", "frequency", "source",
        "destinations", "key_links", "notes")}
    values["credential_ids"] = form.getlist("credential_ids")
    cleaned = repo.clean_entry(values, require_name=require_name)

    credentials = await _vault_credentials(user, membership, company_id)
    if credentials is None:
        # Users without vault access never see the picker, so keep what is stored.
        cleaned["credential_ids"] = list((existing or {}).get("credential_ids") or [])
    else:
        allowed = {item["id"] for item in credentials}
        if any(item not in allowed for item in cleaned["credential_ids"]):
            raise ValueError("Choose encryption keys from this company's credential vault")

    user_id = int(user["id"]) if user.get("id") else None
    app_id = await repo.resolve_app(cleaned, created_by=user_id)
    return cleaned, app_id


async def _audit(request: Request, user: dict, action: str, entity_id: int, metadata: dict[str, Any]) -> None:
    from app.services import audit as audit_service

    await audit_service.log_action(
        action=action, user_id=user.get("id"), entity_type="backup_register",
        entity_id=entity_id, metadata=metadata, request=request,
    )


@router.post("/backups", response_class=HTMLResponse, summary="Create a manual backup entry")
async def create_backup(request: Request):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, membership, _company, company_id, _can_edit = context
    try:
        cleaned, app_id = await _clean_submission(
            request, user, membership, company_id, existing=None, require_name=True)
    except ValueError as exc:
        return flash_redirect("/backups/new", str(exc), "error")
    entry_id = await repo.create_entry(
        company_id, cleaned, backup_app_id=app_id,
        created_by=int(user["id"]) if user.get("id") else None)
    await _audit(request, user, "backup_register.create", entry_id,
                 {"company_id": company_id, "name": cleaned["name"]})
    return flash_redirect("/backups", "Backup added.", "success")


@router.post("/backups/{entry_id}", response_class=HTMLResponse, summary="Update a manual backup entry")
async def update_backup(request: Request, entry_id: int):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, membership, _company, company_id, _can_edit = context
    existing = await repo.get_entry(company_id, entry_id)
    if not existing or existing.get("backup_job_id"):
        return flash_redirect("/backups", "Backup not found.", "error")
    try:
        cleaned, app_id = await _clean_submission(
            request, user, membership, company_id, existing=existing, require_name=True)
    except ValueError as exc:
        return flash_redirect(f"/backups/{int(entry_id)}/edit", str(exc), "error")
    await repo.update_entry(company_id, entry_id, cleaned, backup_app_id=app_id)
    await _audit(request, user, "backup_register.update", entry_id,
                 {"company_id": company_id, "name": cleaned["name"]})
    return flash_redirect("/backups", "Backup updated.", "success")


@router.post("/backups/{entry_id}/delete", response_class=HTMLResponse, summary="Delete a manual backup entry")
async def delete_backup(request: Request, entry_id: int):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, _membership, _company, company_id, _can_edit = context
    existing = await repo.get_entry(company_id, entry_id)
    if not existing or existing.get("backup_job_id"):
        return flash_redirect("/backups", "Backup not found.", "error")
    await repo.delete_entry(company_id, entry_id)
    await _audit(request, user, "backup_register.delete", entry_id,
                 {"company_id": company_id, "name": existing["name"]})
    return flash_redirect("/backups", "Backup deleted.", "success")


@router.post("/backups/tracked/{job_id}", response_class=HTMLResponse,
             summary="Save documentation for a status-tracked backup job")
async def save_tracked_backup(request: Request, job_id: int):
    context = await _context(request, write=True)
    if isinstance(context, RedirectResponse):
        return context
    user, membership, _company, company_id, _can_edit = context
    job = await _company_job(company_id, job_id)
    existing = await repo.get_entry_for_job(job_id)
    try:
        cleaned, app_id = await _clean_submission(
            request, user, membership, company_id, existing=existing, require_name=False)
    except ValueError as exc:
        return flash_redirect(f"/backups/tracked/{int(job_id)}/edit", str(exc), "error")
    # The tracked job's name is managed in Backup History; keep the row in step.
    cleaned["name"] = job["name"]
    if existing:
        await repo.update_job_entry(company_id, existing["id"], cleaned, backup_app_id=app_id)
        entry_id = existing["id"]
    else:
        entry_id = await repo.create_entry(
            company_id, cleaned, backup_app_id=app_id, backup_job_id=int(job_id),
            created_by=int(user["id"]) if user.get("id") else None)
    await _audit(request, user, "backup_register.update", entry_id,
                 {"company_id": company_id, "backup_job_id": int(job_id), "name": job["name"]})
    return flash_redirect("/backups", "Backup details saved.", "success")


__all__ = ["router"]
