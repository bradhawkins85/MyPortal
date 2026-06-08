"""Compliance routes for the ``compliance`` feature pack."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import get_settings
from app.repositories import companies as company_repo
from app.repositories import compliance_checks as cc_repo
from app.repositories import essential8 as essential8_repo
from app.repositories import user_companies as user_company_repo


router = APIRouter(tags=["Compliance"])
settings = get_settings()


@lru_cache(maxsize=1)
def _main():
    from app import main as main_module

    return main_module


async def _load_compliance_context(request: Request):
    """Load context for compliance-related pages.

    Requires user to have can_view_compliance permission.
    """
    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return user, None, None, None, redirect
    is_super_admin = bool(user.get("is_super_admin"))
    company_id_raw = user.get("company_id")
    if company_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No company associated with the current user",
        )
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid company identifier",
        ) from exc
    membership = await user_company_repo.get_user_company(user["id"], company_id)
    can_view = bool(membership and membership.get("can_view_compliance"))
    if not (is_super_admin or can_view):
        return (
            user,
            membership,
            None,
            company_id,
            RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


@router.get("/compliance", response_class=HTMLResponse)
async def compliance_page(request: Request):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_compliance_context(request)
    if redirect:
        return redirect

    compliance_records = await essential8_repo.list_company_compliance(company_id)
    ml_statuses = await essential8_repo.get_per_maturity_statuses_for_company(company_id)
    for record in compliance_records:
        ctrl_ml = ml_statuses.get(
            record["control_id"],
            {"ml1": "not_started", "ml2": "not_started", "ml3": "not_started"},
        )
        record["ml1_status"] = ctrl_ml["ml1"]
        record["ml2_status"] = ctrl_ml["ml2"]
        record["ml3_status"] = ctrl_ml["ml3"]

    summary = await essential8_repo.get_company_compliance_summary(company_id)
    has_compliance_gaps = bool(
        (summary.get("not_started") or 0) > 0 or (summary.get("in_progress") or 0) > 0
    )

    extra = {
        "title": "Essential 8 Compliance",
        "compliance_records": compliance_records,
        "summary": summary,
        "has_compliance_gaps": has_compliance_gaps,
        "company": company,
        "is_super_admin": bool(user.get("is_super_admin")),
        "essential8_compliance_help_url": settings.essential8_compliance_marketing_url,
    }
    return await main_module._render_template("compliance/index.html", request, user, extra=extra)


@router.get("/compliance/control/{control_id}", response_class=HTMLResponse)
async def compliance_control_requirements_page(request: Request, control_id: int):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_compliance_context(request)
    if redirect:
        return redirect

    control_data = await essential8_repo.get_control_with_requirements(
        control_id=control_id,
        company_id=company_id,
    )

    if not control_data:
        raise HTTPException(status_code=404, detail="Control not found")

    requirement_compliance_map = {}
    for rc in control_data.get("requirement_compliance", []):
        requirement_compliance_map[rc["requirement_id"]] = rc

    ml_statuses = await essential8_repo.get_per_maturity_statuses_for_company(company_id)
    ctrl_ml = ml_statuses.get(
        control_id,
        {"ml1": "not_started", "ml2": "not_started", "ml3": "not_started"},
    )

    extra = {
        "title": f"{control_data['control']['name']} - Requirements",
        "control": control_data["control"],
        "requirements_ml1": control_data["requirements_ml1"],
        "requirements_ml2": control_data["requirements_ml2"],
        "requirements_ml3": control_data["requirements_ml3"],
        "company_compliance": control_data.get("company_compliance"),
        "ml1_status": ctrl_ml["ml1"],
        "ml2_status": ctrl_ml["ml2"],
        "ml3_status": ctrl_ml["ml3"],
        "requirement_compliance_map": requirement_compliance_map,
        "company": company,
        "is_super_admin": bool(user.get("is_super_admin")),
    }
    return await main_module._render_template(
        "compliance/control_requirements.html",
        request,
        user,
        extra=extra,
    )


async def _load_compliance_checks_context(request: Request):
    """Load context for compliance checks pages.

    Requires the user to have can_view_compliance_checks permission.
    """
    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return user, None, None, None, redirect
    is_super_admin = bool(user.get("is_super_admin"))
    company_id_raw = user.get("company_id")
    if company_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No company associated with the current user",
        )
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid company identifier",
        ) from exc
    membership = await user_company_repo.get_user_company(user["id"], company_id)
    can_view = bool(membership and membership.get("can_view_compliance_checks"))
    if not (is_super_admin or can_view):
        return (
            user,
            membership,
            None,
            company_id,
            RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )
    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


@router.get("/compliance-checks", response_class=HTMLResponse)
async def compliance_checks_page(request: Request):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_compliance_checks_context(request)
    if redirect:
        return redirect

    is_super_admin = bool(user.get("is_super_admin"))
    can_manage = is_super_admin or bool(membership and membership.get("can_manage_compliance_checks"))
    assignments = await cc_repo.list_assignments(company_id)
    summary = await cc_repo.get_assignment_summary(company_id)
    categories = await cc_repo.list_categories()

    extra = {
        "title": "Compliance Checks",
        "assignments": assignments,
        "summary": summary,
        "categories": categories,
        "company": company,
        "is_super_admin": is_super_admin,
        "can_manage": can_manage,
    }
    return await main_module._render_template("compliance_checks/index.html", request, user, extra=extra)


@router.get("/compliance-checks/{assignment_id}", response_class=HTMLResponse)
async def compliance_checks_detail_page(request: Request, assignment_id: int):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_compliance_checks_context(request)
    if redirect:
        return redirect

    is_super_admin = bool(user.get("is_super_admin"))
    can_manage = is_super_admin or bool(membership and membership.get("can_manage_compliance_checks"))
    assignment = await cc_repo.get_assignment(company_id, assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    evidence_items = await cc_repo.list_evidence(assignment_id)
    audit_trail = await cc_repo.list_audit(assignment_id, limit=50)

    extra = {
        "title": assignment.get("check", {}).get("title", "Compliance Check"),
        "assignment": assignment,
        "evidence_items": evidence_items,
        "audit_trail": audit_trail,
        "company": company,
        "is_super_admin": is_super_admin,
        "can_manage": can_manage,
    }
    return await main_module._render_template("compliance_checks/detail.html", request, user, extra=extra)


@router.get("/admin/compliance-checks/library", response_class=HTMLResponse)
async def compliance_checks_library_page(request: Request):
    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return redirect
    if not user.get("is_super_admin"):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    checks = await cc_repo.list_checks()
    categories = await cc_repo.list_categories()

    extra = {
        "title": "Compliance Checks Library",
        "checks": checks,
        "categories": categories,
        "is_super_admin": True,
    }
    return await main_module._render_template("compliance_checks/library.html", request, user, extra=extra)


__all__ = ["router"]
