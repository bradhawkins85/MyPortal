"""Office 365 group cleanup: find and remove disabled / ex-staff group members."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.logging import log_info
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import m365 as m365_service
from app.services import m365_group_cleanup as cleanup_service
from app.services import m365_jobs as m365_jobs_service

router = APIRouter(tags=["Office365 Group Cleanup"])

PAGE = "/m365/group-cleanup"
MENU_KEY = "menu.m365.group_cleanup"
SCAN_JOB = "group_cleanup_scan"
SCAN_RESOURCE = "groups"
_ACTIVE = {"queued", "running"}


def _main():
    from app import main as main_module
    return main_module


async def _context(request: Request, *, write: bool = False):
    user, redirect = await _main()._require_menu_page_access(
        request,
        MENU_KEY,
        write=write,
        detail="Group cleanup permission required",
    )
    if redirect:
        return None, None, redirect
    company_id = getattr(request.state, "active_company_id", None) or user.get("company_id")
    if company_id is None:
        return user, None, flash_redirect("/", "Select a company first.", "error")
    return user, int(company_id), None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat()
    return str(value) if value else None


@router.get(PAGE, response_class=HTMLResponse)
async def group_cleanup_page(request: Request):
    """List groups and distribution lists that still hold disabled or ex-staff members."""
    user, company_id, redirect = await _context(request)
    if redirect:
        return redirect
    can_write = await _main()._has_menu_page_access(request, user, MENU_KEY, write=True)
    credentials = await m365_service.get_credentials(company_id)
    scan = await cleanup_service.get_latest(company_id)
    job = await m365_jobs_service.latest(company_id, SCAN_JOB)
    result = (scan or {}).get("result") or {}
    groups = result.get("groups") or []
    scan_running = bool(job and job.get("status") in _ACTIVE)
    scan_error = None
    if job and job.get("status") == "failed":
        scanned_at = (scan or {}).get("scanned_at")
        completed_at = job.get("completed_at")
        if not scanned_at or (completed_at and str(completed_at) > str(scanned_at)):
            scan_error = job.get("safe_error") or "The last scan failed."
    return await _main()._render_template("m365/group_cleanup.html", request, user, extra={
        "title": "Group cleanup",
        "has_credentials": bool(credentials),
        "can_write": can_write,
        "scan": scan,
        "scanned_at": _iso((scan or {}).get("scanned_at")),
        "groups": groups,
        "summary": {
            "groups": len(groups),
            "cleanable_groups": sum(1 for g in groups if g.get("can_clean")),
            "removable_entries": sum(int(g.get("removable_count") or 0) for g in groups),
            "removable_people": int(result.get("removable_people") or 0),
            "users_checked": int(result.get("users_checked") or 0),
            "lookup_failures": int(result.get("lookup_failures") or 0),
        },
        "scan_running": scan_running,
        "scan_error": scan_error,
        "reason_labels": cleanup_service.REASON_LABELS,
    })


@router.post(PAGE + "/scan", response_class=RedirectResponse)
async def start_group_cleanup_scan(request: Request):
    """Queue a background scan of the tenant's groups for removable members."""
    user, company_id, redirect = await _context(request, write=True)
    if redirect:
        return redirect
    if not await m365_service.get_credentials(company_id):
        return flash_redirect(PAGE, "Connect Microsoft 365 for this company first.", "error")
    job = await m365_jobs_service.enqueue(company_id, SCAN_JOB, SCAN_RESOURCE)
    log_info("M365 group cleanup scan queued", company_id=company_id, user_id=user.get("id"), job_id=job["id"])
    return flash_redirect(PAGE, "Scan started. This page refreshes when it finishes.", "success")


@router.post(PAGE + "/{group_id}/clean", response_class=RedirectResponse)
async def clean_group(group_id: str, request: Request):
    """Remove disabled and ex-staff members from one group or distribution list.

    Members are re-checked against Microsoft 365 before removal. Send
    ``member_ids`` to remove only some of them; omit it to remove them all.
    """
    user, company_id, redirect = await _context(request, write=True)
    if redirect:
        return redirect
    form = await request.form()
    member_ids = [str(v) for v in form.getlist("member_ids") if str(v).strip()]
    if form.get("scope") == "selected" and not member_ids:
        return flash_redirect(PAGE, "Select at least one member to remove.", "error")
    try:
        outcome = await cleanup_service.clean_group(
            company_id, group_id, member_ids if form.get("scope") == "selected" else None
        )
    except ValueError as exc:
        return flash_redirect(PAGE, str(exc), "error")
    except m365_service.M365Error as exc:
        if exc.http_status == 400:
            return flash_redirect(PAGE, "That group could not be found.", "error")
        if exc.http_status == 404:
            return flash_redirect(PAGE, "That group no longer exists in Microsoft 365. Run a new scan.", "error")
        return flash_redirect(PAGE, "Microsoft 365 could not be reached. Try again later.", "error")

    removed, failed = outcome["removed"], outcome["failed"]
    group = outcome["group"]
    await audit_service.record(
        action="m365.group_cleanup.clean",
        request=request,
        user_id=int(user["id"]),
        entity_type="m365_group",
        entity_id=None,
        after={
            "group_id": group["id"],
            "group_name": group.get("display_name"),
            "group_kind": group.get("kind"),
            "removed": [m["user_principal_name"] or m["id"] for m in removed],
            "failed": [m["user_principal_name"] or m["id"] for m in failed],
        },
    )
    name = group.get("display_name") or "the group"
    if failed and not removed:
        return flash_redirect(PAGE, f"No members were removed from {name}. {failed[0]['error']}", "error")
    if failed:
        return flash_redirect(
            PAGE,
            f"Removed {len(removed)} from {name}; {len(failed)} could not be removed. {failed[0]['error']}",
            "warning",
        )
    if not removed:
        return flash_redirect(PAGE, f"{name} has no disabled or ex-staff members left to remove.", "success")
    plural = "" if len(removed) == 1 else "s"
    return flash_redirect(PAGE, f"Removed {len(removed)} member{plural} from {name}.", "success")
