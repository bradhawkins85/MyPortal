"""
BCP (Business Continuity Planning) routes and page handlers.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.dependencies.auth import get_current_session
from app.core.config import get_settings
from app.repositories import bcp as bcp_repo
from app.repositories import company_memberships as membership_repo
from app.security.session import SessionData

router = APIRouter(prefix="/bcp", tags=["Business Continuity Planning"])

settings = get_settings()


async def _require_bcp_view(request: Request, session: SessionData = Depends(get_current_session)) -> tuple[dict[str, Any], int | None]:
    """Require BCP view permission."""
    from app.repositories import users as user_repo
    
    user = await user_repo.get_user_by_id(session.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    
    # Super admin has access
    if user.get("is_super_admin"):
        active_company_id = getattr(request.state, "active_company_id", None)
        return user, active_company_id
    
    # Check BCP view permission
    has_permission = await membership_repo.user_has_permission(session.user_id, "bcp:view")
    if not has_permission:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="BCP view permission required")
    
    active_company_id = getattr(request.state, "active_company_id", None)
    if active_company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company selected")
    
    return user, active_company_id


async def _require_bcp_edit(request: Request, session: SessionData = Depends(get_current_session)) -> tuple[dict[str, Any], int]:
    """Require BCP edit permission."""
    from app.repositories import users as user_repo
    
    user = await user_repo.get_user_by_id(session.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    
    # Super admin has access
    if user.get("is_super_admin"):
        active_company_id = getattr(request.state, "active_company_id", None)
        if active_company_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company selected")
        return user, active_company_id
    
    # Check BCP edit permission
    has_permission = await membership_repo.user_has_permission(session.user_id, "bcp:edit")
    if not has_permission:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="BCP edit permission required")
    
    active_company_id = getattr(request.state, "active_company_id", None)
    if active_company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company selected")
    
    return user, active_company_id


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def bcp_overview(request: Request):
    """BCP Overview page with PPRR cards."""
    user, company_id = await _require_bcp_view(request)
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        # Create default plan
        plan = await bcp_repo.create_plan(company_id)
        # Seed default objectives
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Get objectives and distribution list
    objectives = await bcp_repo.list_objectives(plan["id"])
    distribution_list = await bcp_repo.list_distribution_list(plan["id"])
    
    # Import template rendering from main
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Business Continuity Plan - Overview",
            "plan": plan,
            "objectives": objectives,
            "distribution_list": distribution_list,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/overview.html", context)


@router.get("/glossary", response_class=HTMLResponse, include_in_schema=False)
async def bcp_glossary(request: Request):
    """BCP Glossary page."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    # Define glossary terms
    glossary_terms = [
        {
            "term": "RTO (Recovery Time Objective)",
            "definition": "The maximum acceptable length of time that a business process can be down following a disaster or disruption.",
        },
        {
            "term": "RPO (Recovery Point Objective)",
            "definition": "The maximum acceptable amount of data loss measured in time before the disaster occurs.",
        },
        {
            "term": "Key Activities",
            "definition": "Critical business functions and processes that must be maintained or quickly restored to ensure business continuity.",
        },
        {
            "term": "Resources",
            "definition": "Personnel, facilities, equipment, information, and other assets required to perform key activities and recover operations.",
        },
        {
            "term": "Risk Management",
            "definition": "The process of identifying, assessing, and controlling threats to an organization's capital and earnings.",
        },
        {
            "term": "Business Impact Analysis (BIA)",
            "definition": "A systematic process to determine the potential effects of an interruption to critical business operations.",
        },
        {
            "term": "Incident Response",
            "definition": "The immediate actions taken to respond to and manage the aftermath of a security breach or cyberattack.",
        },
        {
            "term": "PPRR Framework",
            "definition": "Prevention, Preparedness, Response, and Recovery - a comprehensive approach to emergency management.",
        },
        {
            "term": "Critical Infrastructure",
            "definition": "Assets, systems, and networks that are essential for the functioning of a society and economy.",
        },
        {
            "term": "Continuity Plan",
            "definition": "A documented strategy that outlines procedures and instructions an organization must follow in the face of disaster.",
        },
    ]
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "BCP Glossary",
            "glossary_terms": glossary_terms,
        },
    )
    
    return templates.TemplateResponse("bcp/glossary.html", context)


# Stub pages for downstream features
@router.get("/risks", response_class=HTMLResponse, include_in_schema=False)
async def bcp_risks(request: Request):
    """BCP Risks page (stub)."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Risk Assessment",
            "page_type": "risks",
        },
    )
    
    return templates.TemplateResponse("bcp/stub.html", context)


@router.get("/bia", response_class=HTMLResponse, include_in_schema=False)
async def bcp_bia(request: Request):
    """BCP Business Impact Analysis page (stub)."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Business Impact Analysis",
            "page_type": "bia",
        },
    )
    
    return templates.TemplateResponse("bcp/stub.html", context)


@router.get("/incident", response_class=HTMLResponse, include_in_schema=False)
async def bcp_incident(request: Request):
    """BCP Incident Response page (stub)."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Incident Response",
            "page_type": "incident",
        },
    )
    
    return templates.TemplateResponse("bcp/stub.html", context)


@router.get("/recovery", response_class=HTMLResponse, include_in_schema=False)
async def bcp_recovery(request: Request):
    """BCP Recovery Strategies page (stub)."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Recovery Strategies",
            "page_type": "recovery",
        },
    )
    
    return templates.TemplateResponse("bcp/stub.html", context)


@router.get("/contacts", response_class=HTMLResponse, include_in_schema=False)
async def bcp_contacts(request: Request):
    """BCP Contacts & Claims page (stub)."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Contacts & Claims",
            "page_type": "contacts",
        },
    )
    
    return templates.TemplateResponse("bcp/stub.html", context)


@router.get("/schedules", response_class=HTMLResponse, include_in_schema=False)
async def bcp_schedules(request: Request):
    """BCP Schedules page (stub)."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Review Schedules",
            "page_type": "schedules",
        },
    )
    
    return templates.TemplateResponse("bcp/stub.html", context)


@router.get("/export", response_class=HTMLResponse, include_in_schema=False)
async def bcp_export(request: Request):
    """BCP Export page (stub)."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Export Plan",
            "page_type": "export",
        },
    )
    
    return templates.TemplateResponse("bcp/stub.html", context)


# API endpoints for updating plan data
@router.post("/update", include_in_schema=False)
async def update_plan(
    request: Request,
    title: str = Form(...),
    executive_summary: str = Form(None),
    version: str = Form(None),
):
    """Update BCP plan overview."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bcp_repo.update_plan(
        plan["id"],
        title=title,
        executive_summary=executive_summary if executive_summary else None,
        version=version if version else None,
    )
    
    return RedirectResponse(
        url="/bcp?success=" + quote("Plan updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/objectives", include_in_schema=False)
async def add_objective(
    request: Request,
    objective_text: str = Form(...),
):
    """Add a new objective to the plan."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bcp_repo.create_objective(plan["id"], objective_text)
    
    return RedirectResponse(
        url="/bcp?success=" + quote("Objective added"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/objectives/{objective_id}/delete", include_in_schema=False)
async def delete_objective(
    request: Request,
    objective_id: int,
):
    """Delete an objective."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_objective(objective_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Objective not found")
    
    return RedirectResponse(
        url="/bcp?success=" + quote("Objective deleted"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/distribution", include_in_schema=False)
async def add_distribution_entry(
    request: Request,
    copy_number: str = Form(...),
    name: str = Form(...),
    location: str = Form(None),
):
    """Add a distribution list entry."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bcp_repo.create_distribution_entry(plan["id"], copy_number, name, location)
    
    return RedirectResponse(
        url="/bcp?success=" + quote("Distribution entry added"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/distribution/{entry_id}/delete", include_in_schema=False)
async def delete_distribution_entry(
    request: Request,
    entry_id: int,
):
    """Delete a distribution list entry."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_distribution_entry(entry_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    
    return RedirectResponse(
        url="/bcp?success=" + quote("Distribution entry deleted"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
