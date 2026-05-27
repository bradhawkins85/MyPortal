"""Continuity admin page routes for the ``continuity`` feature pack."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse

from app.repositories import business_continuity_plans as bc_plans_repo
from app.repositories import companies as company_repo
from app.repositories import users as user_repo


router = APIRouter(tags=["Continuity"])


def _main():
    from app import main as main_module

    return main_module


def _add_plan_datetime_iso_fields(plans: list[dict]) -> None:
    """Enrich plan dicts with ISO datetime strings for template rendering."""
    for plan in plans:
        for source_field, target_field in (
            ("updated_at", "updated_at_iso"),
            ("last_reviewed_at", "last_reviewed_at_iso"),
        ):
            value = plan.get(source_field)
            if value:
                plan[target_field] = (
                    value.isoformat() if isinstance(value, datetime) else str(value)
                )


@router.get("/admin/business-continuity-plans", response_class=HTMLResponse)
async def admin_business_continuity_plans_page(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    plans = await bc_plans_repo.list_plans()
    _add_plan_datetime_iso_fields(plans)

    extra = {
        "title": "Business Continuity Plans",
        "bc_plans": jsonable_encoder(plans),
    }
    return await main_module._render_template(
        "admin/business_continuity_plans.html",
        request,
        current_user,
        extra=extra,
    )


@router.get("/admin/business-continuity-plans/new", response_class=HTMLResponse)
async def admin_new_business_continuity_plan_page(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    users = await user_repo.list_users()
    companies = await company_repo.list_companies()

    extra = {
        "title": "New Business Continuity Plan",
        "plan": None,
        "users": jsonable_encoder(users),
        "companies": jsonable_encoder(companies),
    }
    return await main_module._render_template(
        "admin/business_continuity_plan_editor.html",
        request,
        current_user,
        extra=extra,
    )


@router.get("/admin/business-continuity-plans/{plan_id}", response_class=HTMLResponse)
async def admin_edit_business_continuity_plan_page(request: Request, plan_id: int):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    plan = await bc_plans_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    users = await user_repo.list_users()
    companies = await company_repo.list_companies()

    extra = {
        "title": f"Edit Plan · {plan.get('title') or f'Plan #{plan_id}'}",
        "plan": jsonable_encoder(plan),
        "users": jsonable_encoder(users),
        "companies": jsonable_encoder(companies),
    }
    return await main_module._render_template(
        "admin/business_continuity_plan_editor.html",
        request,
        current_user,
        extra=extra,
    )


__all__ = ["router"]
