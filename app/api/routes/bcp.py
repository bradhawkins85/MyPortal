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


def _check_bcp_enabled():
    """Check if BCP module is enabled."""
    if not settings.bcp_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BCP module is not enabled"
        )


async def _require_bcp_view(request: Request, session: SessionData = Depends(get_current_session)) -> tuple[dict[str, Any], int | None]:
    """Require BCP view permission."""
    _check_bcp_enabled()
    
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
    _check_bcp_enabled()
    
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


# Risk Assessment pages and endpoints
@router.get("/risks", response_class=HTMLResponse, include_in_schema=False)
async def bcp_risks(request: Request, severity: str = Query(None), heatmap_filter: str = Query(None)):
    """BCP Risks page with list, heatmap, and legend."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    from app.services.risk_calculator import (
        get_severity_band_info,
        get_likelihood_scale,
        get_impact_scale,
    )
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Get all risks
    all_risks = await bcp_repo.list_risks(plan["id"])
    
    # Apply filters
    risks = all_risks
    if severity:
        risks = [r for r in risks if r.get("severity") == severity]
    if heatmap_filter:
        # Format is "likelihood,impact"
        try:
            likelihood, impact = map(int, heatmap_filter.split(","))
            risks = [r for r in risks if r.get("likelihood") == likelihood and r.get("impact") == impact]
        except (ValueError, AttributeError):
            pass
    
    # Get heatmap data
    heatmap_data = await bcp_repo.get_risk_heatmap_data(plan["id"])
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Risk Assessment",
            "plan": plan,
            "risks": risks,
            "heatmap_data": heatmap_data,
            "severity_bands": get_severity_band_info(),
            "likelihood_scale": get_likelihood_scale(),
            "impact_scale": get_impact_scale(),
            "active_severity_filter": severity,
            "active_heatmap_filter": heatmap_filter,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/risks.html", context)


@router.get("/bia", response_class=HTMLResponse, include_in_schema=False)
async def bcp_bia(request: Request, sort_by: str = Query("importance")):
    """BCP Business Impact Analysis page with critical activities list."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    from app.services.time_utils import humanize_hours
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Validate sort_by parameter
    valid_sorts = ["importance", "priority", "name"]
    if sort_by not in valid_sorts:
        sort_by = "importance"
    
    # Get all critical activities with their impacts
    activities = await bcp_repo.list_critical_activities(plan["id"], sort_by=sort_by)
    
    # Add humanized RTO to each activity
    for activity in activities:
        if activity.get("impact") and activity["impact"].get("rto_hours") is not None:
            activity["impact"]["rto_humanized"] = humanize_hours(activity["impact"]["rto_hours"])
        else:
            activity["impact_rto_humanized"] = "-" if not activity.get("impact") else None
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Business Impact Analysis",
            "plan": plan,
            "activities": activities,
            "sort_by": sort_by,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/bia.html", context)


@router.get("/incident", response_class=HTMLResponse, include_in_schema=False)
async def bcp_incident(request: Request, tab: str = Query("checklist")):
    """BCP Incident Console with tabs for Checklist, Contacts, and Event Log."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
        await bcp_repo.seed_default_checklist_items(plan["id"])
    
    # Check if there are checklist items; if not, seed them
    checklist_items = await bcp_repo.list_checklist_items(plan["id"], phase="Immediate")
    if not checklist_items:
        await bcp_repo.seed_default_checklist_items(plan["id"])
        checklist_items = await bcp_repo.list_checklist_items(plan["id"], phase="Immediate")
    
    # Get active incident if any
    active_incident = await bcp_repo.get_active_incident(plan["id"])
    
    # Get checklist ticks if there's an active incident
    checklist_with_ticks = []
    if active_incident:
        ticks = await bcp_repo.get_checklist_ticks_for_incident(active_incident["id"])
        # Map ticks by checklist_item_id for easy lookup
        tick_map = {tick["checklist_item_id"]: tick for tick in ticks}
        
        for item in checklist_items:
            tick = tick_map.get(item["id"])
            checklist_with_ticks.append({
                **item,
                "tick": tick,
            })
    else:
        # No active incident, just show items without ticks
        checklist_with_ticks = [{**item, "tick": None} for item in checklist_items]
    
    # Get contacts
    contacts = await bcp_repo.list_contacts(plan["id"])
    internal_contacts = [c for c in contacts if c["kind"] == "Internal"]
    external_contacts = [c for c in contacts if c["kind"] == "External"]
    
    # Get event log entries
    event_log = []
    if active_incident:
        event_log = await bcp_repo.list_event_log_entries(plan["id"], incident_id=active_incident["id"])
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Incident Console",
            "plan": plan,
            "active_incident": active_incident,
            "checklist_items": checklist_with_ticks,
            "internal_contacts": internal_contacts,
            "external_contacts": external_contacts,
            "event_log": event_log,
            "active_tab": tab,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/incident.html", context)


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


@router.post("/risks", include_in_schema=False)
async def create_risk(
    request: Request,
    description: str = Form(...),
    likelihood: int = Form(...),
    impact: int = Form(...),
    preventative_actions: str = Form(None),
    contingency_plans: str = Form(None),
):
    """Create a new risk."""
    user, company_id = await _require_bcp_edit(request)
    
    from app.services.risk_calculator import calculate_risk
    
    # Validate likelihood and impact
    if not (1 <= likelihood <= 4):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Likelihood must be between 1 and 4")
    if not (1 <= impact <= 4):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Impact must be between 1 and 4")
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Calculate risk rating and severity
    rating, severity = calculate_risk(likelihood, impact)
    
    await bcp_repo.create_risk(
        plan["id"],
        description,
        likelihood,
        impact,
        rating,
        severity,
        preventative_actions if preventative_actions else None,
        contingency_plans if contingency_plans else None,
    )
    
    return RedirectResponse(
        url="/bcp/risks?success=" + quote("Risk created successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/risks/{risk_id}/update", include_in_schema=False)
async def update_risk(
    request: Request,
    risk_id: int,
    description: str = Form(...),
    likelihood: int = Form(...),
    impact: int = Form(...),
    preventative_actions: str = Form(None),
    contingency_plans: str = Form(None),
):
    """Update a risk."""
    user, company_id = await _require_bcp_edit(request)
    
    from app.services.risk_calculator import calculate_risk
    
    # Validate likelihood and impact
    if not (1 <= likelihood <= 4):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Likelihood must be between 1 and 4")
    if not (1 <= impact <= 4):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Impact must be between 1 and 4")
    
    # Calculate risk rating and severity
    rating, severity = calculate_risk(likelihood, impact)
    
    updated = await bcp_repo.update_risk(
        risk_id,
        description=description,
        likelihood=likelihood,
        impact=impact,
        rating=rating,
        severity=severity,
        preventative_actions=preventative_actions if preventative_actions else None,
        contingency_plans=contingency_plans if contingency_plans else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Risk not found")
    
    return RedirectResponse(
        url="/bcp/risks?success=" + quote("Risk updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/risks/{risk_id}/delete", include_in_schema=False)
async def delete_risk(
    request: Request,
    risk_id: int,
):
    """Delete a risk."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_risk(risk_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Risk not found")
    
    return RedirectResponse(
        url="/bcp/risks?success=" + quote("Risk deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/risks/export", include_in_schema=False)
async def export_risks_csv(request: Request):
    """Export risks to CSV."""
    user, company_id = await _require_bcp_view(request)
    
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    risks = await bcp_repo.list_risks(plan["id"])
    
    # Create CSV
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "ID",
            "Description",
            "Likelihood",
            "Impact",
            "Rating",
            "Severity",
            "Preventative Actions",
            "Contingency Plans",
            "Created At",
            "Updated At",
        ],
    )
    writer.writeheader()
    
    for risk in risks:
        writer.writerow({
            "ID": risk["id"],
            "Description": risk["description"],
            "Likelihood": risk.get("likelihood", ""),
            "Impact": risk.get("impact", ""),
            "Rating": risk.get("rating", ""),
            "Severity": risk.get("severity", ""),
            "Preventative Actions": risk.get("preventative_actions") or "",
            "Contingency Plans": risk.get("contingency_plans") or "",
            "Created At": risk.get("created_at", ""),
            "Updated At": risk.get("updated_at", ""),
        })
    
    output.seek(0)
    
    from datetime import datetime as dt
    filename = f"risk_register_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/risks/seed", include_in_schema=False)
async def seed_risks(request: Request):
    """Seed example risks."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bcp_repo.seed_example_risks(plan["id"])
    
    return RedirectResponse(
        url="/bcp/risks?success=" + quote("Example risks added successfully"),
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


# ============================================================================
# Insurance Pages and Endpoints
# ============================================================================


@router.get("/insurance", response_class=HTMLResponse, include_in_schema=False)
async def bcp_insurance(request: Request):
    """BCP Insurance page with policies table."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Get all insurance policies
    policies = await bcp_repo.list_insurance_policies(plan["id"])
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Insurance Policies",
            "plan": plan,
            "policies": policies,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/insurance.html", context)


@router.post("/insurance", include_in_schema=False)
async def create_insurance_policy(
    request: Request,
    policy_type: str = Form(...),
    coverage: str = Form(None),
    exclusions: str = Form(None),
    insurer: str = Form(None),
    contact: str = Form(None),
    last_review_date: str = Form(None),
    payment_terms: str = Form(None),
):
    """Create a new insurance policy."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Parse date if provided
    review_date = None
    if last_review_date:
        try:
            from datetime import datetime
            review_date = datetime.fromisoformat(last_review_date)
        except ValueError:
            pass
    
    await bcp_repo.create_insurance_policy(
        plan["id"],
        policy_type,
        coverage if coverage else None,
        exclusions if exclusions else None,
        insurer if insurer else None,
        contact if contact else None,
        review_date,
        payment_terms if payment_terms else None,
    )
    
    return RedirectResponse(
        url="/bcp/insurance?success=" + quote("Insurance policy created successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/insurance/{policy_id}/update", include_in_schema=False)
async def update_insurance_policy(
    request: Request,
    policy_id: int,
    policy_type: str = Form(...),
    coverage: str = Form(None),
    exclusions: str = Form(None),
    insurer: str = Form(None),
    contact: str = Form(None),
    last_review_date: str = Form(None),
    payment_terms: str = Form(None),
):
    """Update an insurance policy."""
    user, company_id = await _require_bcp_edit(request)
    
    # Parse date if provided
    review_date = None
    if last_review_date:
        try:
            from datetime import datetime
            review_date = datetime.fromisoformat(last_review_date)
        except ValueError:
            pass
    
    updated = await bcp_repo.update_insurance_policy(
        policy_id,
        policy_type=policy_type,
        coverage=coverage if coverage else None,
        exclusions=exclusions if exclusions else None,
        insurer=insurer if insurer else None,
        contact=contact if contact else None,
        last_review_date=review_date,
        payment_terms=payment_terms if payment_terms else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    
    return RedirectResponse(
        url="/bcp/insurance?success=" + quote("Insurance policy updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/insurance/{policy_id}/delete", include_in_schema=False)
async def delete_insurance_policy(
    request: Request,
    policy_id: int,
):
    """Delete an insurance policy."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_insurance_policy(policy_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    
    return RedirectResponse(
        url="/bcp/insurance?success=" + quote("Insurance policy deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/insurance/export", include_in_schema=False)
async def export_insurance_csv(request: Request):
    """Export insurance policies to CSV."""
    user, company_id = await _require_bcp_view(request)
    
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    policies = await bcp_repo.list_insurance_policies(plan["id"])
    
    # Create CSV
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "ID",
            "Type",
            "Coverage",
            "Exclusions",
            "Insurer & Contact",
            "Last Review Date",
            "Payment Terms",
            "Created At",
            "Updated At",
        ],
    )
    writer.writeheader()
    
    for policy in policies:
        writer.writerow({
            "ID": policy["id"],
            "Type": policy["type"],
            "Coverage": policy.get("coverage") or "",
            "Exclusions": policy.get("exclusions") or "",
            "Insurer & Contact": f"{policy.get('insurer') or ''} - {policy.get('contact') or ''}",
            "Last Review Date": policy.get("last_review_date", ""),
            "Payment Terms": policy.get("payment_terms") or "",
            "Created At": policy.get("created_at", ""),
            "Updated At": policy.get("updated_at", ""),
        })
    
    output.seek(0)
    
    from datetime import datetime as dt
    filename = f"insurance_policies_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============================================================================
# Backup Pages and Endpoints
# ============================================================================


@router.get("/backups", response_class=HTMLResponse, include_in_schema=False)
async def bcp_backups(request: Request):
    """BCP Backups page with backup items table."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Get all backup items
    backups = await bcp_repo.list_backup_items(plan["id"])
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Data Backup Strategy",
            "plan": plan,
            "backups": backups,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/backups.html", context)


@router.post("/backups", include_in_schema=False)
async def create_backup_item(
    request: Request,
    data_scope: str = Form(...),
    frequency: str = Form(None),
    medium: str = Form(None),
    owner: str = Form(None),
    steps: str = Form(None),
):
    """Create a new backup item."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bcp_repo.create_backup_item(
        plan["id"],
        data_scope,
        frequency if frequency else None,
        medium if medium else None,
        owner if owner else None,
        steps if steps else None,
    )
    
    return RedirectResponse(
        url="/bcp/backups?success=" + quote("Backup item created successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/backups/{backup_id}/update", include_in_schema=False)
async def update_backup_item(
    request: Request,
    backup_id: int,
    data_scope: str = Form(...),
    frequency: str = Form(None),
    medium: str = Form(None),
    owner: str = Form(None),
    steps: str = Form(None),
):
    """Update a backup item."""
    user, company_id = await _require_bcp_edit(request)
    
    updated = await bcp_repo.update_backup_item(
        backup_id,
        data_scope=data_scope,
        frequency=frequency if frequency else None,
        medium=medium if medium else None,
        owner=owner if owner else None,
        steps=steps if steps else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup item not found")
    
    return RedirectResponse(
        url="/bcp/backups?success=" + quote("Backup item updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/backups/{backup_id}/delete", include_in_schema=False)
async def delete_backup_item(
    request: Request,
    backup_id: int,
):
    """Delete a backup item."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_backup_item(backup_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup item not found")
    
    return RedirectResponse(
        url="/bcp/backups?success=" + quote("Backup item deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/backups/export", include_in_schema=False)
async def export_backups_csv(request: Request):
    """Export backup items to CSV."""
    user, company_id = await _require_bcp_view(request)
    
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    backups = await bcp_repo.list_backup_items(plan["id"])
    
    # Create CSV
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "ID",
            "Data for Backup",
            "Frequency",
            "Media/Service",
            "Responsible Person",
            "Procedure Steps",
            "Created At",
            "Updated At",
        ],
    )
    writer.writeheader()
    
    for backup in backups:
        writer.writerow({
            "ID": backup["id"],
            "Data for Backup": backup["data_scope"],
            "Frequency": backup.get("frequency") or "",
            "Media/Service": backup.get("medium") or "",
            "Responsible Person": backup.get("owner") or "",
            "Procedure Steps": backup.get("steps") or "",
            "Created At": backup.get("created_at", ""),
            "Updated At": backup.get("updated_at", ""),
        })
    
    output.seek(0)
    
    from datetime import datetime as dt
    filename = f"backup_items_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============================================================================
# Business Impact Analysis (BIA) Pages and Endpoints
# ============================================================================


@router.get("/bia/new", response_class=HTMLResponse, include_in_schema=False)
async def bcp_bia_new(request: Request):
    """New critical activity page."""
    user, company_id = await _require_bcp_edit(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Add Critical Activity",
            "plan": plan,
            "activity": None,
            "can_edit": True,
        },
    )
    
    return templates.TemplateResponse("bcp/bia_edit.html", context)


@router.get("/bia/{activity_id}/edit", response_class=HTMLResponse, include_in_schema=False)
async def bcp_bia_edit(request: Request, activity_id: int):
    """Edit page for a critical activity."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    from app.services.time_utils import humanize_hours
    
    # Get the activity
    activity = await bcp_repo.get_critical_activity_by_id(activity_id)
    if not activity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")
    
    # Verify it belongs to the user's company plan
    plan = await bcp_repo.get_plan_by_id(activity["plan_id"])
    if not plan or plan["company_id"] != company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": f"Edit Critical Activity: {activity['name']}",
            "plan": plan,
            "activity": activity,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/bia_edit.html", context)


@router.post("/bia", include_in_schema=False)
async def create_critical_activity(
    request: Request,
    name: str = Form(...),
    description: str = Form(None),
    priority: str = Form(None),
    supplier_dependency: str = Form(None),
    importance: int = Form(None),
    notes: str = Form(None),
    # Impact fields
    losses_financial: str = Form(None),
    losses_increased_costs: str = Form(None),
    losses_staffing: str = Form(None),
    losses_product_service: str = Form(None),
    losses_reputation: str = Form(None),
    fines: str = Form(None),
    legal_liability: str = Form(None),
    rto_hours: int = Form(None),
    losses_comments: str = Form(None),
):
    """Create a new critical activity with impact data."""
    user, company_id = await _require_bcp_edit(request)
    
    # Validate importance if provided
    if importance is not None and not (1 <= importance <= 5):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Importance must be between 1 and 5"
        )
    
    # Validate RTO if provided
    if rto_hours is not None and rto_hours < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="RTO hours must be non-negative"
        )
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Create the critical activity
    activity = await bcp_repo.create_critical_activity(
        plan["id"],
        name,
        description if description else None,
        priority if priority else None,
        supplier_dependency if supplier_dependency else None,
        importance,
        notes if notes else None,
    )
    
    # Create impact data if any impact fields provided
    has_impact = any([
        losses_financial, losses_increased_costs, losses_staffing,
        losses_product_service, losses_reputation, fines, legal_liability,
        rto_hours is not None, losses_comments
    ])
    
    if has_impact:
        await bcp_repo.create_or_update_impact(
            activity["id"],
            losses_financial if losses_financial else None,
            losses_increased_costs if losses_increased_costs else None,
            losses_staffing if losses_staffing else None,
            losses_product_service if losses_product_service else None,
            losses_reputation if losses_reputation else None,
            fines if fines else None,
            legal_liability if legal_liability else None,
            rto_hours,
            losses_comments if losses_comments else None,
        )
    
    return RedirectResponse(
        url="/bcp/bia?success=" + quote("Critical activity created successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/bia/{activity_id}/update", include_in_schema=False)
async def update_critical_activity_endpoint(
    request: Request,
    activity_id: int,
    name: str = Form(...),
    description: str = Form(None),
    priority: str = Form(None),
    supplier_dependency: str = Form(None),
    importance: int = Form(None),
    notes: str = Form(None),
    # Impact fields
    losses_financial: str = Form(None),
    losses_increased_costs: str = Form(None),
    losses_staffing: str = Form(None),
    losses_product_service: str = Form(None),
    losses_reputation: str = Form(None),
    fines: str = Form(None),
    legal_liability: str = Form(None),
    rto_hours: int = Form(None),
    losses_comments: str = Form(None),
):
    """Update a critical activity with impact data."""
    user, company_id = await _require_bcp_edit(request)
    
    # Validate importance if provided
    if importance is not None and not (1 <= importance <= 5):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Importance must be between 1 and 5"
        )
    
    # Validate RTO if provided
    if rto_hours is not None and rto_hours < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="RTO hours must be non-negative"
        )
    
    # Update the activity
    updated = await bcp_repo.update_critical_activity(
        activity_id,
        name=name,
        description=description if description else None,
        priority=priority if priority else None,
        supplier_dependency=supplier_dependency if supplier_dependency else None,
        importance=importance,
        notes=notes if notes else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")
    
    # Update impact data
    await bcp_repo.create_or_update_impact(
        activity_id,
        losses_financial if losses_financial else None,
        losses_increased_costs if losses_increased_costs else None,
        losses_staffing if losses_staffing else None,
        losses_product_service if losses_product_service else None,
        losses_reputation if losses_reputation else None,
        fines if fines else None,
        legal_liability if legal_liability else None,
        rto_hours,
        losses_comments if losses_comments else None,
    )
    
    return RedirectResponse(
        url="/bcp/bia?success=" + quote("Critical activity updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/bia/{activity_id}/delete", include_in_schema=False)
async def delete_critical_activity_endpoint(
    request: Request,
    activity_id: int,
):
    """Delete a critical activity."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_critical_activity(activity_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")
    
    return RedirectResponse(
        url="/bcp/bia?success=" + quote("Critical activity deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/bia/export", include_in_schema=False)
async def export_bia_csv(request: Request):
    """Export BIA summary to CSV."""
    user, company_id = await _require_bcp_view(request)
    
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO
    from app.services.time_utils import humanize_hours
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    activities = await bcp_repo.list_critical_activities(plan["id"], sort_by="importance")
    
    # Create CSV
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "Activity",
            "Description",
            "Priority",
            "Importance",
            "RTO",
            "Supplier Dependency",
            "Financial Impact",
            "Increased Costs",
            "Staffing Impact",
            "Product/Service Impact",
            "Reputation Impact",
            "Fines/Penalties",
            "Legal Liability",
            "Additional Comments",
        ],
    )
    writer.writeheader()
    
    for activity in activities:
        impact = activity.get("impact") or {}
        rto_humanized = humanize_hours(impact.get("rto_hours"))
        
        writer.writerow({
            "Activity": activity["name"],
            "Description": activity.get("description") or "",
            "Priority": activity.get("priority") or "",
            "Importance": activity.get("importance") or "",
            "RTO": rto_humanized,
            "Supplier Dependency": activity.get("supplier_dependency") or "",
            "Financial Impact": impact.get("losses_financial") or "",
            "Increased Costs": impact.get("losses_increased_costs") or "",
            "Staffing Impact": impact.get("losses_staffing") or "",
            "Product/Service Impact": impact.get("losses_product_service") or "",
            "Reputation Impact": impact.get("losses_reputation") or "",
            "Fines/Penalties": impact.get("fines") or "",
            "Legal Liability": impact.get("legal_liability") or "",
            "Additional Comments": impact.get("losses_comments") or "",
        })
    
    output.seek(0)
    
    from datetime import datetime as dt
    filename = f"bia_summary_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============================================================================
# Incident Console Endpoints
# ============================================================================


@router.post("/incident/start", include_in_schema=False)
async def start_incident(request: Request):
    """Start a new incident."""
    user, company_id = await _require_bcp_edit(request)
    
    from datetime import datetime
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Check if there's already an active incident
    active_incident = await bcp_repo.get_active_incident(plan["id"])
    if active_incident:
        return RedirectResponse(
            url="/bcp/incident?error=" + quote("An incident is already active"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    # Create new incident
    now = datetime.utcnow()
    incident = await bcp_repo.create_incident(plan["id"], now, source="Manual")
    
    # Initialize checklist ticks
    await bcp_repo.initialize_checklist_ticks(plan["id"], incident["id"])
    
    # Create initial event log entry
    # Get user initials
    user_name = user.get("name", "")
    initials = "".join([part[0].upper() for part in user_name.split()[:2]]) if user_name else "SYS"
    
    await bcp_repo.create_event_log_entry(
        plan["id"],
        incident["id"],
        now,
        "Activate business continuity plan",
        author_id=user["id"],
        initials=initials,
    )
    
    # TODO: Send portal alert to distribution list
    # This would require implementing a notification/alert system
    
    return RedirectResponse(
        url="/bcp/incident?success=" + quote("Incident started successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/incident/close", include_in_schema=False)
async def close_incident_endpoint(request: Request):
    """Close the active incident."""
    user, company_id = await _require_bcp_edit(request)
    
    from datetime import datetime
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get active incident
    active_incident = await bcp_repo.get_active_incident(plan["id"])
    if not active_incident:
        return RedirectResponse(
            url="/bcp/incident?error=" + quote("No active incident found"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    # Close the incident
    await bcp_repo.close_incident(active_incident["id"])
    
    # Add event log entry
    now = datetime.utcnow()
    user_name = user.get("name", "")
    initials = "".join([part[0].upper() for part in user_name.split()[:2]]) if user_name else "SYS"
    
    await bcp_repo.create_event_log_entry(
        plan["id"],
        active_incident["id"],
        now,
        "Incident closed",
        author_id=user["id"],
        initials=initials,
    )
    
    return RedirectResponse(
        url="/bcp/incident?success=" + quote("Incident closed successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/incident/checklist/{tick_id}/toggle", include_in_schema=False)
async def toggle_checklist_item(
    request: Request,
    tick_id: int,
):
    """Toggle a checklist item."""
    user, company_id = await _require_bcp_edit(request)
    
    from datetime import datetime
    
    # Get the tick
    tick = await bcp_repo.get_checklist_tick_by_id(tick_id)
    if not tick:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checklist item not found")
    
    # Toggle the tick
    new_state = not tick["is_done"]
    now = datetime.utcnow()
    
    await bcp_repo.toggle_checklist_tick(tick_id, new_state, user["id"], now)
    
    return RedirectResponse(
        url="/bcp/incident?tab=checklist",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/incident/contacts", include_in_schema=False)
async def create_contact_endpoint(
    request: Request,
    kind: str = Form(...),
    person_or_org: str = Form(...),
    phones: str = Form(None),
    email: str = Form(None),
    responsibility_or_agency: str = Form(None),
):
    """Create a new contact."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bcp_repo.create_contact(
        plan["id"],
        kind,
        person_or_org,
        phones if phones else None,
        email if email else None,
        responsibility_or_agency if responsibility_or_agency else None,
    )
    
    return RedirectResponse(
        url="/bcp/incident?tab=contacts&success=" + quote("Contact added successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/incident/contacts/{contact_id}/update", include_in_schema=False)
async def update_contact_endpoint(
    request: Request,
    contact_id: int,
    kind: str = Form(...),
    person_or_org: str = Form(...),
    phones: str = Form(None),
    email: str = Form(None),
    responsibility_or_agency: str = Form(None),
):
    """Update a contact."""
    user, company_id = await _require_bcp_edit(request)
    
    updated = await bcp_repo.update_contact(
        contact_id,
        kind=kind,
        person_or_org=person_or_org,
        phones=phones if phones else None,
        email=email if email else None,
        responsibility_or_agency=responsibility_or_agency if responsibility_or_agency else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    
    return RedirectResponse(
        url="/bcp/incident?tab=contacts&success=" + quote("Contact updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/incident/contacts/{contact_id}/delete", include_in_schema=False)
async def delete_contact_endpoint(
    request: Request,
    contact_id: int,
):
    """Delete a contact."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_contact(contact_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    
    return RedirectResponse(
        url="/bcp/incident?tab=contacts&success=" + quote("Contact deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/incident/event-log", include_in_schema=False)
async def create_event_log_entry_endpoint(
    request: Request,
    notes: str = Form(...),
    happened_at: str = Form(None),
):
    """Create a new event log entry."""
    user, company_id = await _require_bcp_edit(request)
    
    from datetime import datetime
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get active incident
    active_incident = await bcp_repo.get_active_incident(plan["id"])
    if not active_incident:
        return RedirectResponse(
            url="/bcp/incident?tab=event-log&error=" + quote("No active incident"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    
    # Parse timestamp or use current time
    if happened_at:
        try:
            event_time = datetime.fromisoformat(happened_at.replace('Z', '+00:00'))
        except ValueError:
            event_time = datetime.utcnow()
    else:
        event_time = datetime.utcnow()
    
    # Get user initials
    user_name = user.get("name", "")
    initials = "".join([part[0].upper() for part in user_name.split()[:2]]) if user_name else "USR"
    
    await bcp_repo.create_event_log_entry(
        plan["id"],
        active_incident["id"],
        event_time,
        notes,
        author_id=user["id"],
        initials=initials,
    )
    
    return RedirectResponse(
        url="/bcp/incident?tab=event-log&success=" + quote("Event logged successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/incident/event-log/export", include_in_schema=False)
async def export_event_log_csv(request: Request):
    """Export event log to CSV."""
    user, company_id = await _require_bcp_view(request)
    
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get active incident
    active_incident = await bcp_repo.get_active_incident(plan["id"])
    if not active_incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active incident")
    
    event_log = await bcp_repo.list_event_log_entries(plan["id"], incident_id=active_incident["id"])
    
    # Create CSV
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "Timestamp",
            "Initials",
            "Notes",
        ],
    )
    writer.writeheader()
    
    for entry in reversed(event_log):  # Reverse to show chronological order
        writer.writerow({
            "Timestamp": entry.get("happened_at", ""),
            "Initials": entry.get("initials") or "",
            "Notes": entry.get("notes", ""),
        })
    
    output.seek(0)
    
    from datetime import datetime as dt
    filename = f"event_log_incident_{active_incident['id']}_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============================================================================
# Webhook Endpoint for External Integrations (e.g., Uptime Kuma)
# ============================================================================


@router.post("/api/webhook/incident/start", include_in_schema=True)
async def webhook_start_incident(request: Request):
    """
    Webhook endpoint to auto-start an incident from external monitoring systems.
    
    Expected JSON payload:
    {
        "company_id": 1,
        "source": "UptimeKuma",
        "message": "Service down alert",
        "api_key": "your-api-key"
    }
    
    Returns:
        dict: Incident details and status
    """
    import json
    from datetime import datetime
    
    # Parse JSON payload
    try:
        body = await request.body()
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload"
        )
    
    # Validate required fields
    company_id = payload.get("company_id")
    source = payload.get("source", "Other")
    message = payload.get("message", "External alert triggered incident")
    api_key = payload.get("api_key")
    
    if not company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="company_id is required"
        )
    
    # TODO: Validate API key against stored keys
    # For now, we'll just check if it's provided
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="api_key is required"
        )
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No BCP plan found for company_id {company_id}"
        )
    
    # Check if there's already an active incident
    active_incident = await bcp_repo.get_active_incident(plan["id"])
    if active_incident:
        return {
            "status": "already_active",
            "incident_id": active_incident["id"],
            "message": "An incident is already active for this plan"
        }
    
    # Create new incident
    now = datetime.utcnow()
    incident = await bcp_repo.create_incident(plan["id"], now, source=source)
    
    # Initialize checklist ticks
    await bcp_repo.initialize_checklist_ticks(plan["id"], incident["id"])
    
    # Create initial event log entry
    await bcp_repo.create_event_log_entry(
        plan["id"],
        incident["id"],
        now,
        f"Incident auto-started via {source}: {message}",
        author_id=None,
        initials="SYS",
    )
    
    # TODO: Send portal alert to distribution list
    # This would require implementing a notification/alert system
    
    return {
        "status": "started",
        "incident_id": incident["id"],
        "plan_id": plan["id"],
        "started_at": incident["started_at"].isoformat() if incident["started_at"] else None,
        "message": "Incident started successfully"
    }
