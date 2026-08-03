"""Configurable dashboard layout and panel-data API."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.repositories import dashboard_layouts as layouts_repo
from app.repositories import reporting as reporting_repo
from app.services import dashboard_layouts as layouts_service
from app.services.system_variables import get_system_variables

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


def _company_id(request: Request) -> int | None:
    value = getattr(request.state, "active_company_id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _editable(user: dict, request: Request) -> bool:
    # Every authenticated user owns a personal dashboard layout. Report
    # visibility is enforced separately by the catalog and resolver.
    return bool(user.get("id"))


@router.get("")
async def get_dashboard(request: Request, user: dict = Depends(get_current_user)):
    user_id = int(user["id"])
    personal = await layouts_repo.get_personal(user_id)
    company_id = _company_id(request)
    company = await layouts_repo.get_company(company_id) if company_id else None
    source = "personal" if personal else "company" if company else "default"
    layout = layouts_service.validate_layout(
        personal or company or layouts_service.DEFAULT_LAYOUT
    )
    resolved = await layouts_service.resolve_layout(
        layout,
        company_id=company_id,
        can_run_all=bool(user.get("is_super_admin")),
        user_id=user_id,
    )
    return {
        "layout": resolved,
        "source": source,
        "editable": await _editable(user, request),
        "can_assign_company": bool(user.get("is_super_admin")),
    }


@router.put("")
async def save_dashboard(
    payload: dict[str, Any], request: Request, user: dict = Depends(get_current_user)
):
    if not await _editable(user, request):
        raise HTTPException(
            status_code=403, detail="Dashboard editing requires technician access."
        )
    try:
        layout = layouts_service.validate_layout(payload)
    except layouts_service.InvalidDashboardLayout as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await layouts_repo.set_personal(int(user["id"]), layout)
    return {"layout": layout, "source": "personal"}


@router.post("/resolve")
async def resolve_dashboard(
    payload: dict[str, Any], request: Request, user: dict = Depends(get_current_user)
):
    """Resolve an edited, unsaved layout so its panels can preview live data."""
    try:
        layout = layouts_service.validate_layout(payload)
    except layouts_service.InvalidDashboardLayout as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    resolved = await layouts_service.resolve_layout(
        layout,
        company_id=_company_id(request),
        can_run_all=bool(user.get("is_super_admin")),
        user_id=int(user["id"]),
    )
    return {"layout": resolved}


@router.delete("")
async def reset_dashboard(user: dict = Depends(get_current_user)):
    await layouts_repo.delete_personal(int(user["id"]))
    return {"reset": True}


@router.get("/catalog")
async def dashboard_catalog(user: dict = Depends(get_current_user)):
    reports = await (
        reporting_repo.list_queries()
        if user.get("is_super_admin")
        else reporting_repo.list_queries_for_user(int(user["id"]))
    )
    variables = get_system_variables()
    return {
        "reports": [
            {"slug": r["slug"], "name": r["name"], "description": r.get("description")}
            for r in reports
        ],
        "variables": [{"name": k, "preview": v} for k, v in sorted(variables.items())],
    }


@router.put("/companies/{company_id}")
async def assign_company_dashboard(
    company_id: int, payload: dict[str, Any], user: dict = Depends(require_super_admin)
):
    try:
        layout = layouts_service.validate_layout(payload)
    except layouts_service.InvalidDashboardLayout as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await layouts_repo.set_company(company_id, layout, int(user["id"]))
    return {"company_id": company_id, "layout": layout}
