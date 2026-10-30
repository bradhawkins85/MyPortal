"""Policies page for Microsoft 365 standards (super administrators only)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.security.flash import flash_redirect
from app.services import m365_policies as policy_service

router = APIRouter(tags=["Microsoft 365 Policies"])

_PAGE_URL = "/m365/policies"


def _main():
    from app import main as main_module

    return main_module


def _json_field(form: Any, name: str) -> Any:
    raw = str(form.get(name) or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise ValueError("The policy editor sent data that could not be read. Reload the page and try again.") from None


def policy_from_form(form: Any) -> dict[str, Any]:
    """Build a policy payload from the editor form; the service validates it."""
    standards = [
        {"standard_id": standard_id, "mode": str(form.get("mode_" + standard_id) or "report")}
        for standard_id in form.getlist("standard")
    ]
    overrides = _json_field(form, "overrides_json") or []
    if not isinstance(overrides, list):
        raise ValueError("Company exceptions must be a list")
    return {
        "name": str(form.get("name") or ""),
        "description": str(form.get("description") or ""),
        "enabled": form.get("enabled") == "1",
        "target_rules": _json_field(form, "target_rules_json"),
        "standards": standards,
        "overrides": overrides,
    }


RESULT_STATUSES = ("drift", "failed", "remediated", "compliant", "unknown")
RESULTS_PAGE_SIZE = 50


def filter_results(
    results: list[dict[str, Any]], *, q: str = "", status: str = "", page: int = 1
) -> dict[str, Any]:
    """Search, filter and paginate the tenant results table."""
    needle = q.strip().casefold()
    rows = [
        row for row in results
        if (not status or row["status"] == status)
        and (not needle or needle in " ".join(
            str(row.get(key) or "") for key in ("company_name", "standard_name", "policy_name", "details")
        ).casefold())
    ]
    total_pages = max(1, -(-len(rows) // RESULTS_PAGE_SIZE))
    page = min(max(page, 1), total_pages)
    start = (page - 1) * RESULTS_PAGE_SIZE
    return {"rows": rows[start:start + RESULTS_PAGE_SIZE], "total": len(rows),
            "page": page, "total_pages": total_pages}


@router.get(_PAGE_URL, response_class=HTMLResponse)
async def policies_page(request: Request, q: str = "", status: str = "", page: int = 1):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    data = await policy_service.page_data()
    status = status if status in RESULT_STATUSES else ""
    return await _main()._render_template(
        "m365/policies.html",
        request,
        user,
        extra={
            "title": "M365 Policies",
            **data,
            "result_page": filter_results(data["results"], q=q[:200], status=status, page=page),
            "result_filters": {"q": q[:200], "status": status},
            "result_statuses": RESULT_STATUSES,
        },
    )


@router.post(_PAGE_URL)
async def save_policy(request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    raw_id = str(form.get("policy_id") or "").strip()
    policy_id = int(raw_id) if raw_id.isdigit() else None
    try:
        await policy_service.save_policy_audited(
            policy_from_form(form), policy_id=policy_id, user_id=user.get("id"), request=request
        )
    except LookupError:
        return flash_redirect(_PAGE_URL, "That policy no longer exists.", "error")
    except ValueError as exc:
        return flash_redirect(_PAGE_URL, str(exc), "error")
    return flash_redirect(_PAGE_URL, "Policy saved. It applies on the next run.", "success")


@router.post(_PAGE_URL + "/run")
async def run_policies(request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    policy_service.start_background_run()
    return flash_redirect(_PAGE_URL, "Checking every tenant now. Refresh in a few minutes to see results.", "success")


@router.post(_PAGE_URL + "/{policy_id}/delete")
async def delete_policy(policy_id: int, request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        policy = await policy_service.delete_policy(policy_id, user_id=user.get("id"), request=request)
    except LookupError:
        return flash_redirect(_PAGE_URL, "That policy no longer exists.", "error")
    return flash_redirect(_PAGE_URL, f"Deleted {policy['name']}.", "success")
