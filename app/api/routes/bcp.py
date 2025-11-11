"""
BCP (Business Continuity Planning) routes and page handlers.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
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


async def _resolve_session(request: Request, session: SessionData | None) -> SessionData:
    """Resolve the active session for the current request."""
    if session is not None:
        return session

    cached: SessionData | None = getattr(request.state, "session", None)
    if cached is not None:
        return cached

    return await get_current_session(request)


async def _require_bcp_view(
    request: Request,
    session: SessionData | None = None,
) -> tuple[dict[str, Any], int | None]:
    """Require BCP view permission."""
    _check_bcp_enabled()

    from app.repositories import users as user_repo

    session = await _resolve_session(request, session)

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


async def _require_bcp_edit(
    request: Request,
    session: SessionData | None = None,
) -> tuple[dict[str, Any], int]:
    """Require BCP edit permission."""
    _check_bcp_enabled()

    from app.repositories import users as user_repo

    session = await _resolve_session(request, session)

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


async def _require_bcp_incident_run(
    request: Request,
    session: SessionData | None = None,
) -> tuple[dict[str, Any], int]:
    """Require BCP incident:run permission for incident operations."""
    _check_bcp_enabled()

    from app.repositories import users as user_repo

    session = await _resolve_session(request, session)

    user = await user_repo.get_user_by_id(session.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    
    # Super admin has access
    if user.get("is_super_admin"):
        active_company_id = getattr(request.state, "active_company_id", None)
        if active_company_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company selected")
        return user, active_company_id
    
    # Check BCP incident:run permission
    has_permission = await membership_repo.user_has_permission(session.user_id, "bcp:incident:run")
    if not has_permission:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="BCP incident:run permission required")
    
    active_company_id = getattr(request.state, "active_company_id", None)
    if active_company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company selected")
    
    return user, active_company_id


async def _require_bcp_export(
    request: Request,
    session: SessionData | None = None,
) -> tuple[dict[str, Any], int | None]:
    """Require BCP export permission."""
    _check_bcp_enabled()

    from app.repositories import users as user_repo

    session = await _resolve_session(request, session)

    user = await user_repo.get_user_by_id(session.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    
    # Super admin has access
    if user.get("is_super_admin"):
        active_company_id = getattr(request.state, "active_company_id", None)
        return user, active_company_id
    
    # Check BCP export permission
    has_permission = await membership_repo.user_has_permission(session.user_id, "bcp:export")
    if not has_permission:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="BCP export permission required")
    
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
        # Create default plan with all seed data
        from app.services.bcp_seeding import seed_new_plan_defaults
        plan = await bcp_repo.create_plan(company_id)
        await seed_new_plan_defaults(plan["id"])
    
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


@router.get("/risks/heatmap", response_class=HTMLResponse, include_in_schema=False)
async def bcp_risks_heatmap_partial(request: Request):
    """Return just the heatmap HTML for HTMX updates."""
    user, company_id = await _require_bcp_view(request)
    
    from fastapi.templating import Jinja2Templates
    from app.core.config import get_templates_config
    from app.services.risk_calculator import get_severity_band_info
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Get heatmap data
    heatmap_data = await bcp_repo.get_risk_heatmap_data(plan["id"])
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = {
        "request": request,
        "heatmap_data": heatmap_data,
        "severity_bands": get_severity_band_info(),
    }
    
    return templates.TemplateResponse("bcp/heatmap_partial.html", context)


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
    
    # Get roles with assignments for contacts tab
    from app.repositories import users as user_repo
    roles = await bcp_repo.list_roles_with_assignments(plan["id"])
    # Enrich assignments with user details
    for role in roles:
        for assignment in role["assignments"]:
            user_data = await user_repo.get_user_by_id(assignment["user_id"])
            assignment["user"] = user_data
    
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
            "roles": roles,
            "event_log": event_log,
            "active_tab": tab,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/incident.html", context)


@router.get("/recovery", response_class=HTMLResponse, include_in_schema=False)
async def bcp_recovery(
    request: Request,
    owner_filter: int = Query(None),
    status_filter: str = Query(None),
    activity_filter: int = Query(None),
):
    """BCP Recovery Actions page with filters."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    from app.repositories import users as user_repo
    from app.services.time_utils import humanize_hours
    from datetime import datetime
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Apply filters
    overdue_only = status_filter == "overdue"
    completed_only = status_filter == "completed"
    
    # Get recovery actions
    actions = await bcp_repo.list_recovery_actions(
        plan["id"],
        owner_id=owner_filter,
        overdue_only=overdue_only,
        completed_only=completed_only,
        critical_activity_id=activity_filter,
    )
    
    # Enrich actions with user details and humanized RTO
    for action in actions:
        if action["owner_id"]:
            owner = await user_repo.get_user_by_id(action["owner_id"])
            action["owner"] = owner
        else:
            action["owner"] = None
        
        if action["rto_hours"] is not None:
            action["rto_humanized"] = humanize_hours(action["rto_hours"])
        else:
            action["rto_humanized"] = "-"
    
    # Get all users for owner filter dropdown
    all_users = await user_repo.list_users()
    
    # Get all critical activities for activity filter
    activities = await bcp_repo.list_critical_activities(plan["id"], sort_by="name")
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Recovery Actions",
            "plan": plan,
            "actions": actions,
            "all_users": all_users,
            "activities": activities,
            "owner_filter": owner_filter,
            "status_filter": status_filter,
            "activity_filter": activity_filter,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
            "now": datetime.utcnow(),
        },
    )
    
    return templates.TemplateResponse("bcp/recovery.html", context)


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
    """BCP Training & Review Schedules page."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Get training and review items
    training_items = await bcp_repo.list_training_items(plan["id"])
    review_items = await bcp_repo.list_review_items(plan["id"])
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Training & Review Schedules",
            "plan": plan,
            "training_items": training_items,
            "review_items": review_items,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/schedules.html", context)


@router.post("/training", include_in_schema=False)
async def create_training_item_endpoint(
    request: Request,
    training_date: str = Form(...),
    training_type: str = Form(None),
    comments: str = Form(None),
):
    """Create a new training item."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Parse training date
    try:
        from datetime import datetime
        training_date_obj = datetime.fromisoformat(training_date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid training date format"
        )
    
    await bcp_repo.create_training_item(
        plan["id"],
        training_date_obj,
        training_type if training_type else None,
        comments if comments else None,
    )
    
    return RedirectResponse(
        url="/bcp/schedules?success=" + quote("Training scheduled successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/training/{training_id}/update", include_in_schema=False)
async def update_training_item_endpoint(
    request: Request,
    training_id: int,
    training_date: str = Form(...),
    training_type: str = Form(None),
    comments: str = Form(None),
):
    """Update a training item."""
    user, company_id = await _require_bcp_edit(request)
    
    # Parse training date
    try:
        from datetime import datetime
        training_date_obj = datetime.fromisoformat(training_date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid training date format"
        )
    
    updated = await bcp_repo.update_training_item(
        training_id,
        training_date=training_date_obj,
        training_type=training_type if training_type else None,
        comments=comments if comments else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training item not found")
    
    return RedirectResponse(
        url="/bcp/schedules?success=" + quote("Training updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/training/{training_id}/delete", include_in_schema=False)
async def delete_training_item_endpoint(
    request: Request,
    training_id: int,
):
    """Delete a training item."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_training_item(training_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training item not found")
    
    return RedirectResponse(
        url="/bcp/schedules?success=" + quote("Training deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/review", include_in_schema=False)
async def create_review_item_endpoint(
    request: Request,
    review_date: str = Form(...),
    reason: str = Form(None),
    changes_made: str = Form(None),
):
    """Create a new review item."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Parse review date
    try:
        from datetime import datetime
        review_date_obj = datetime.fromisoformat(review_date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid review date format"
        )
    
    await bcp_repo.create_review_item(
        plan["id"],
        review_date_obj,
        reason if reason else None,
        changes_made if changes_made else None,
    )
    
    return RedirectResponse(
        url="/bcp/schedules?success=" + quote("Review scheduled successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/review/{review_id}/update", include_in_schema=False)
async def update_review_item_endpoint(
    request: Request,
    review_id: int,
    review_date: str = Form(...),
    reason: str = Form(None),
    changes_made: str = Form(None),
):
    """Update a review item."""
    user, company_id = await _require_bcp_edit(request)
    
    # Parse review date
    try:
        from datetime import datetime
        review_date_obj = datetime.fromisoformat(review_date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid review date format"
        )
    
    updated = await bcp_repo.update_review_item(
        review_id,
        review_date=review_date_obj,
        reason=reason if reason else None,
        changes_made=changes_made if changes_made else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")
    
    return RedirectResponse(
        url="/bcp/schedules?success=" + quote("Review updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/review/{review_id}/delete", include_in_schema=False)
async def delete_review_item_endpoint(
    request: Request,
    review_id: int,
):
    """Delete a review item."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_review_item(review_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")
    
    return RedirectResponse(
        url="/bcp/schedules?success=" + quote("Review deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/roles", response_class=HTMLResponse, include_in_schema=False)
async def bcp_roles(request: Request):
    """BCP Roles & Responsibilities page."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    from app.repositories import users as user_repo
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Get roles with assignments
    roles = await bcp_repo.list_roles_with_assignments(plan["id"])
    
    # Enrich assignments with user details
    for role in roles:
        for assignment in role["assignments"]:
            user_data = await user_repo.get_user_by_id(assignment["user_id"])
            assignment["user"] = user_data
    
    # Get all users for assignment dropdown
    all_users = await user_repo.list_users()
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Roles & Responsibilities",
            "plan": plan,
            "roles": roles,
            "all_users": all_users,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/roles.html", context)


@router.post("/roles", include_in_schema=False)
async def create_bcp_role(
    request: Request,
    title: str = Form(...),
    responsibilities: str = Form(None),
):
    """Create a new BCP role."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bcp_repo.create_role(plan["id"], title, responsibilities)
    
    return RedirectResponse(
        url="/bcp/roles?success=" + quote("Role created successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/roles/{role_id}/update", include_in_schema=False)
async def update_bcp_role(
    request: Request,
    role_id: int,
    title: str = Form(...),
    responsibilities: str = Form(None),
):
    """Update a BCP role."""
    user, company_id = await _require_bcp_edit(request)
    
    updated = await bcp_repo.update_role(role_id, title=title, responsibilities=responsibilities)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    
    return RedirectResponse(
        url="/bcp/roles?success=" + quote("Role updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/roles/{role_id}/delete", include_in_schema=False)
async def delete_bcp_role(
    request: Request,
    role_id: int,
):
    """Delete a BCP role."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_role(role_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    
    return RedirectResponse(
        url="/bcp/roles?success=" + quote("Role deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/roles/{role_id}/assign", include_in_schema=False)
async def assign_user_to_role(
    request: Request,
    role_id: int,
    user_id: int = Form(...),
    is_alternate: bool = Form(False),
    contact_info: str = Form(None),
):
    """Assign a user to a BCP role."""
    user, company_id = await _require_bcp_edit(request)
    
    await bcp_repo.create_role_assignment(role_id, user_id, is_alternate, contact_info)
    
    return RedirectResponse(
        url="/bcp/roles?success=" + quote("User assigned to role successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/roles/assignments/{assignment_id}/update", include_in_schema=False)
async def update_role_assignment_endpoint(
    request: Request,
    assignment_id: int,
    user_id: int = Form(...),
    is_alternate: bool = Form(False),
    contact_info: str = Form(None),
):
    """Update a role assignment."""
    user, company_id = await _require_bcp_edit(request)
    
    updated = await bcp_repo.update_role_assignment(assignment_id, user_id=user_id, is_alternate=is_alternate, contact_info=contact_info)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    
    return RedirectResponse(
        url="/bcp/roles?success=" + quote("Assignment updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/roles/assignments/{assignment_id}/delete", include_in_schema=False)
async def delete_role_assignment_endpoint(
    request: Request,
    assignment_id: int,
):
    """Delete a role assignment."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_role_assignment(assignment_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    
    return RedirectResponse(
        url="/bcp/roles?success=" + quote("Assignment removed successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/roles/seed", include_in_schema=False)
async def seed_example_role(request: Request):
    """Seed the example Team Leader role."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bcp_repo.seed_example_team_leader_role(plan["id"])
    
    return RedirectResponse(
        url="/bcp/roles?success=" + quote("Example Team Leader role added"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


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


@router.get("/export/pdf", include_in_schema=True)
async def export_bcp_pdf(
    request: Request,
    event_log_limit: int = Query(100, ge=1, le=500, description="Maximum number of event log entries to include"),
):
    """
    Export BCP to template-faithful PDF format.
    
    Generates a comprehensive PDF export that mirrors the BCP template structure
    with all required sections in the prescribed order. Includes configurable
    event log entries (default 100, max 500).
    
    The PDF includes:
    - Plan Overview (Executive summary, Objectives, Distribution list)
    - Risk Management (Legend, risk register, Insurance, Data backup)
    - Business Impact Analysis (Critical activities, BIA summary with RTO)
    - Incident Response (Checklist, Evacuation, Emergency kit, Roles, Contacts, Event log)
    - Recovery (Actions, Crisis checklist, Contacts, Insurance claims, Market assessment, Wellbeing)
    - Rehearse/Maintain/Review (Training & Review schedules)
    
    Footer includes attribution: "Adapted from the Business Queensland Business continuity plan – Template (CC BY 4.0)"
    
    Args:
        request: FastAPI request object
        event_log_limit: Number of event log entries to include (default 100, max 500)
        
    Returns:
        StreamingResponse with PDF file download
        
    Raises:
        HTTPException: If plan not found or export fails
    """
    from fastapi.responses import StreamingResponse
    from datetime import datetime as dt
    from app.services.bc_export_service import export_bcp_to_pdf
    from app.services import audit
    
    user, company_id = await _require_bcp_export(request)
    
    # Get plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No BCP plan found for this company"
        )
    
    try:
        # Generate PDF
        pdf_buffer, content_hash = await export_bcp_to_pdf(plan["id"], event_log_limit=event_log_limit)
        
        # Create filename
        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        safe_title = "".join(c for c in plan["title"] if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_title = safe_title.replace(' ', '_')
        filename = f"BCP_{safe_title}_{timestamp}.pdf"
        
        # Audit log the PDF export
        await audit.log_action(
            action="bcp.export.pdf",
            user_id=user["id"],
            entity_type="bcp_plan",
            entity_id=plan["id"],
            metadata={"company_id": company_id, "event_log_limit": event_log_limit, "filename": filename},
            request=request,
        )
        
        # Return as streaming response
        pdf_buffer.seek(0)
        return StreamingResponse(
            iter([pdf_buffer.getvalue()]),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "X-Content-Hash": content_hash,
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate PDF export: {str(e)}"
        )


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
    
    
    # Audit log
    await audit.log_action(
        action="bcp.plan.update",
        user_id=user["id"],
        entity_type="plan",
        metadata={"company_id": company_id},
        request=request,
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
    
    
    # Audit log
    await audit.log_action(
        action="bcp.objective.create",
        user_id=user["id"],
        entity_type="objective",
        metadata={"company_id": company_id},
        request=request,
    )
    
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
    
    
    # Audit log
    await audit.log_action(
        action="bcp.objective.delete",
        user_id=user["id"],
        entity_type="objective",
        metadata={"company_id": company_id},
        request=request,
    )
    
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
    from app.services import audit
    
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
    
    
    # Audit log
    await audit.log_action(
        action="bcp.risk.create",
        user_id=user["id"],
        entity_type="risk",
        metadata={"company_id": company_id},
        request=request,
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
    
    
    # Audit log
    await audit.log_action(
        action="bcp.risk.update",
        user_id=user["id"],
        entity_type="risk",
        metadata={"company_id": company_id},
        request=request,
    )
    
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
    
    
    # Audit log
    await audit.log_action(
        action="bcp.risk.delete",
        user_id=user["id"],
        entity_type="risk",
        metadata={"company_id": company_id},
        request=request,
    )
    
    return RedirectResponse(
        url="/bcp/risks?success=" + quote("Risk deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/risks/export", include_in_schema=False)
async def export_risks_csv(request: Request):
    """Export risks to CSV."""
    user, company_id = await _require_bcp_export(request)
    
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO
    from app.services import audit
    
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
    
    # Audit log the CSV export
    await audit.log_action(
        action="bcp.export.risks_csv",
        user_id=user["id"],
        entity_type="bcp_plan",
        entity_id=plan["id"],
        metadata={"company_id": company_id, "filename": filename, "record_count": len(risks)},
        request=request,
    )
    
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
    user, company_id = await _require_bcp_export(request)
    
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
    user, company_id = await _require_bcp_export(request)
    
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
    user, company_id = await _require_bcp_export(request)
    
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
    user, company_id = await _require_bcp_incident_run(request)
    
    from datetime import datetime
    from app.services import audit
    
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
    
    # Audit log the incident start
    await audit.log_action(
        action="bcp.incident.start",
        user_id=user["id"],
        entity_type="bcp_incident",
        entity_id=incident["id"],
        new_value={"plan_id": plan["id"], "source": "Manual"},
        metadata={"company_id": company_id},
        request=request,
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
    user, company_id = await _require_bcp_incident_run(request)
    
    from datetime import datetime
    from app.services import audit
    
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
    
    # Audit log the incident closure
    await audit.log_action(
        action="bcp.incident.close",
        user_id=user["id"],
        entity_type="bcp_incident",
        entity_id=active_incident["id"],
        metadata={"plan_id": plan["id"], "company_id": company_id},
        request=request,
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
    user, company_id = await _require_bcp_incident_run(request)
    
    from datetime import datetime
    from app.services import audit
    
    # Get the tick
    tick = await bcp_repo.get_checklist_tick_by_id(tick_id)
    if not tick:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checklist item not found")
    
    # Toggle the tick
    new_state = not tick["is_done"]
    now = datetime.utcnow()
    
    await bcp_repo.toggle_checklist_tick(tick_id, new_state, user["id"], now)
    
    # Audit log the checklist toggle
    await audit.log_action(
        action="bcp.checklist.toggle",
        user_id=user["id"],
        entity_type="bcp_checklist_tick",
        entity_id=tick_id,
        previous_value={"is_done": tick["is_done"]},
        new_value={"is_done": new_state},
        metadata={"company_id": company_id, "incident_id": tick.get("incident_id")},
        request=request,
    )
    
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
    user, company_id = await _require_bcp_incident_run(request)
    
    from datetime import datetime
    from app.services import audit
    
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
    
    # Audit log the event log entry
    await audit.log_action(
        action="bcp.event_log.create",
        user_id=user["id"],
        entity_type="bcp_event_log",
        entity_id=active_incident["id"],
        new_value={"notes": notes, "happened_at": event_time.isoformat()},
        metadata={"plan_id": plan["id"], "company_id": company_id, "incident_id": active_incident["id"]},
        request=request,
    )
    
    return RedirectResponse(
        url="/bcp/incident?tab=event-log&success=" + quote("Event logged successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/incident/event-log/export", include_in_schema=False)
async def export_event_log_csv(request: Request):
    """Export event log to CSV."""
    user, company_id = await _require_bcp_export(request)
    
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


# ============================================================================
# Evacuation Procedures Pages and Endpoints
# ============================================================================


@router.get("/evacuation", response_class=HTMLResponse, include_in_schema=False)
async def bcp_evacuation(request: Request):
    """BCP Evacuation Procedures page."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Get or create evacuation plan
    evacuation = await bcp_repo.get_evacuation_plan(plan["id"])
    if not evacuation:
        evacuation = await bcp_repo.create_evacuation_plan(plan["id"])
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Evacuation Procedures",
            "plan": plan,
            "evacuation": evacuation,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/evacuation.html", context)


@router.post("/evacuation", include_in_schema=False)
async def update_evacuation(
    request: Request,
    meeting_point: str = Form(None),
    notes: str = Form(None),
):
    """Update evacuation plan."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Get or create evacuation plan
    evacuation = await bcp_repo.get_evacuation_plan(plan["id"])
    if not evacuation:
        evacuation = await bcp_repo.create_evacuation_plan(plan["id"])
    
    # Update evacuation plan
    await bcp_repo.update_evacuation_plan(
        evacuation["id"],
        meeting_point=meeting_point if meeting_point else None,
        notes=notes if notes else None,
    )
    
    return RedirectResponse(
        url="/bcp/evacuation?success=" + quote("Evacuation plan updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ============================================================================
# Emergency Kit Pages and Endpoints
# ============================================================================


@router.get("/emergency-kit", response_class=HTMLResponse, include_in_schema=False)
async def bcp_emergency_kit(request: Request, tab: str = Query("documents")):
    """BCP Emergency Kit page with Documents and Equipment tabs."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Check if items exist; if not, seed them
    all_items = await bcp_repo.list_emergency_kit_items(plan["id"])
    if not all_items:
        await bcp_repo.seed_default_emergency_kit_items(plan["id"])
        all_items = await bcp_repo.list_emergency_kit_items(plan["id"])
    
    # Filter items by category
    document_items = [item for item in all_items if item["category"] == "Document"]
    equipment_items = [item for item in all_items if item["category"] == "Equipment"]
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Emergency Kit",
            "plan": plan,
            "document_items": document_items,
            "equipment_items": equipment_items,
            "active_tab": tab,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/emergency_kit.html", context)


@router.post("/emergency-kit", include_in_schema=False)
async def create_emergency_kit_item_endpoint(
    request: Request,
    category: str = Form(...),
    name: str = Form(...),
    notes: str = Form(None),
):
    """Create a new emergency kit item."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bcp_repo.create_emergency_kit_item(
        plan["id"],
        category,
        name,
        notes if notes else None,
    )
    
    # Redirect to the appropriate tab
    tab = "documents" if category == "Document" else "equipment"
    return RedirectResponse(
        url=f"/bcp/emergency-kit?tab={tab}&success=" + quote("Item added successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/emergency-kit/{item_id}/update", include_in_schema=False)
async def update_emergency_kit_item_endpoint(
    request: Request,
    item_id: int,
    category: str = Form(...),
    name: str = Form(...),
    notes: str = Form(None),
):
    """Update an emergency kit item."""
    user, company_id = await _require_bcp_edit(request)
    
    updated = await bcp_repo.update_emergency_kit_item(
        item_id,
        category=category,
        name=name,
        notes=notes if notes else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    
    # Redirect to the appropriate tab
    tab = "documents" if category == "Document" else "equipment"
    return RedirectResponse(
        url=f"/bcp/emergency-kit?tab={tab}&success=" + quote("Item updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/emergency-kit/{item_id}/check", include_in_schema=False)
async def mark_emergency_kit_item_checked_endpoint(
    request: Request,
    item_id: int,
):
    """Mark an emergency kit item as checked today."""
    user, company_id = await _require_bcp_edit(request)
    
    from datetime import datetime
    
    # Get the item to determine its category for redirect
    item = await bcp_repo.get_emergency_kit_item_by_id(item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    
    # Mark as checked
    await bcp_repo.mark_emergency_kit_item_checked(item_id, datetime.utcnow())
    
    # Redirect to the appropriate tab
    tab = "documents" if item["category"] == "Document" else "equipment"
    return RedirectResponse(
        url=f"/bcp/emergency-kit?tab={tab}&success=" + quote("Item marked as checked"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/emergency-kit/{item_id}/delete", include_in_schema=False)
async def delete_emergency_kit_item_endpoint(
    request: Request,
    item_id: int,
):
    """Delete an emergency kit item."""
    user, company_id = await _require_bcp_edit(request)
    
    # Get the item to determine its category for redirect
    item = await bcp_repo.get_emergency_kit_item_by_id(item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    
    tab = "documents" if item["category"] == "Document" else "equipment"
    
    deleted = await bcp_repo.delete_emergency_kit_item(item_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    
    return RedirectResponse(
        url=f"/bcp/emergency-kit?tab={tab}&success=" + quote("Item deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ============================================================================
# Recovery Actions Endpoints
# ============================================================================


@router.post("/recovery", include_in_schema=False)
async def create_recovery_action_endpoint(
    request: Request,
    action: str = Form(...),
    resources: str = Form(None),
    owner_id: int = Form(None),
    rto_hours: int = Form(None),
    due_date: str = Form(None),
    critical_activity_id: int = Form(None),
):
    """Create a new recovery action."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Parse due date if provided
    due_date_obj = None
    if due_date:
        try:
            from datetime import datetime
            due_date_obj = datetime.fromisoformat(due_date)
        except ValueError:
            pass
    
    # Validate RTO if provided
    if rto_hours is not None and rto_hours < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="RTO hours must be non-negative"
        )
    
    await bcp_repo.create_recovery_action(
        plan["id"],
        action,
        resources if resources else None,
        owner_id if owner_id else None,
        rto_hours if rto_hours else None,
        due_date_obj,
        critical_activity_id if critical_activity_id else None,
    )
    
    
    # Audit logging
    await audit.log_action(
        action="bcp.recovery_action.create",
        user_id=user["id"],
        entity_type="recovery_action",
        entity_id=None,
        metadata={"company_id": company_id},
        request=request,
    )
    
    return RedirectResponse(
        url="/bcp/recovery?success=" + quote("Recovery action created successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/recovery/{action_id}/update", include_in_schema=False)
async def update_recovery_action_endpoint(
    request: Request,
    action_id: int,
    action: str = Form(...),
    resources: str = Form(None),
    owner_id: int = Form(None),
    rto_hours: int = Form(None),
    due_date: str = Form(None),
    critical_activity_id: int = Form(None),
):
    """Update a recovery action."""
    user, company_id = await _require_bcp_edit(request)
    
    # Parse due date if provided
    due_date_obj = None
    if due_date:
        try:
            from datetime import datetime
            due_date_obj = datetime.fromisoformat(due_date)
        except ValueError:
            pass
    
    # Validate RTO if provided
    if rto_hours is not None and rto_hours < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="RTO hours must be non-negative"
        )
    
    updated = await bcp_repo.update_recovery_action(
        action_id,
        action=action,
        resources=resources if resources else None,
        owner_id=owner_id if owner_id else None,
        rto_hours=rto_hours if rto_hours else None,
        due_date=due_date_obj,
        critical_activity_id=critical_activity_id if critical_activity_id else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recovery action not found")
    
    
    # Audit logging
    await audit.log_action(
        action="bcp.recovery_action.update",
        user_id=user["id"],
        entity_type="recovery_action",
        entity_id=action_id,
        metadata={"company_id": company_id},
        request=request,
    )
    
    return RedirectResponse(
        url="/bcp/recovery?success=" + quote("Recovery action updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/recovery/{action_id}/complete", include_in_schema=False)
async def mark_recovery_action_complete_endpoint(
    request: Request,
    action_id: int,
):
    """Mark a recovery action as completed."""
    user, company_id = await _require_bcp_edit(request)
    
    from datetime import datetime
    
    updated = await bcp_repo.mark_recovery_action_complete(action_id, datetime.utcnow())
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recovery action not found")
    
    
    # Audit logging
    await audit.log_action(
        action="bcp.recovery_action.complete",
        user_id=user["id"],
        entity_type="recovery_action",
        entity_id=action_id,
        metadata={"company_id": company_id},
        request=request,
    )
    
    return RedirectResponse(
        url="/bcp/recovery?success=" + quote("Recovery action marked as complete"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/recovery/{action_id}/delete", include_in_schema=False)
async def delete_recovery_action_endpoint(
    request: Request,
    action_id: int,
):
    """Delete a recovery action."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_recovery_action(action_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recovery action not found")
    
    
    # Audit logging
    await audit.log_action(
        action="bcp.recovery_action.delete",
        user_id=user["id"],
        entity_type="recovery_action",
        entity_id=action_id,
        metadata={"company_id": company_id},
        request=request,
    )
    
    return RedirectResponse(
        url="/bcp/recovery?success=" + quote("Recovery action deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/recovery/export", include_in_schema=False)
async def export_recovery_actions_csv(request: Request):
    """Export recovery actions to CSV."""
    user, company_id = await _require_bcp_export(request)
    
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO
    from app.repositories import users as user_repo
    from app.services.time_utils import humanize_hours
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    actions = await bcp_repo.list_recovery_actions(plan["id"])
    
    # Create CSV
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "ID",
            "Action",
            "Critical Activity",
            "Resources/Outcomes",
            "RTO",
            "Owner",
            "Due Date",
            "Completed At",
            "Created At",
            "Updated At",
        ],
    )
    writer.writeheader()
    
    for action in actions:
        # Get owner name if exists
        owner_name = ""
        if action["owner_id"]:
            owner = await user_repo.get_user_by_id(action["owner_id"])
            if owner:
                owner_name = owner.get("name", "")
        
        # Humanize RTO
        rto_humanized = humanize_hours(action["rto_hours"]) if action["rto_hours"] is not None else ""
        
        writer.writerow({
            "ID": action["id"],
            "Action": action["action"],
            "Critical Activity": action.get("activity_name") or "",
            "Resources/Outcomes": action.get("resources") or "",
            "RTO": rto_humanized,
            "Owner": owner_name,
            "Due Date": action.get("due_date", ""),
            "Completed At": action.get("completed_at", ""),
            "Created At": action.get("created_at", ""),
            "Updated At": action.get("updated_at", ""),
        })
    
    output.seek(0)
    
    from datetime import datetime as dt
    filename = f"recovery_actions_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============================================================================
# Recovery Checklist Page and Endpoints
# ============================================================================


@router.get("/recovery/checklist", response_class=HTMLResponse, include_in_schema=False)
async def bcp_recovery_checklist(request: Request):
    """BCP Recovery Checklist page for Crisis & Recovery phase."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Check if there are Crisis/Recovery checklist items; if not, seed them
    checklist_items = await bcp_repo.list_checklist_items(plan["id"], phase="CrisisRecovery")
    if not checklist_items:
        await bcp_repo.seed_default_crisis_recovery_checklist_items(plan["id"])
        checklist_items = await bcp_repo.list_checklist_items(plan["id"], phase="CrisisRecovery")
    
    # Get active incident if any
    active_incident = await bcp_repo.get_active_incident(plan["id"])
    
    # Get checklist ticks if there's an active incident
    checklist_with_ticks = []
    if active_incident:
        ticks = await bcp_repo.get_checklist_ticks_for_incident(active_incident["id"])
        # Map ticks by checklist_item_id for easy lookup
        tick_map = {tick["checklist_item_id"]: tick for tick in ticks}
        
        # Create ticks for any items that don't have them yet
        for item in checklist_items:
            if item["id"] not in tick_map:
                # Create a new tick for this item
                query = """
                    INSERT INTO bcp_checklist_tick 
                    (plan_id, checklist_item_id, incident_id, is_done)
                    VALUES (%s, %s, %s, FALSE)
                """
                from app.core.database import db
                async with db.connection() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute(query, (plan["id"], item["id"], active_incident["id"]))
                        await conn.commit()
        
        # Re-fetch ticks to include newly created ones
        ticks = await bcp_repo.get_checklist_ticks_for_incident(active_incident["id"])
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
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Crisis & Recovery Checklist",
            "plan": plan,
            "active_incident": active_incident,
            "checklist_items": checklist_with_ticks,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/recovery_checklist.html", context)


# ============================================================================
# Recovery Contacts Page and Endpoints
# ============================================================================


@router.get("/recovery-contacts", response_class=HTMLResponse, include_in_schema=False)
async def bcp_recovery_contacts(request: Request):
    """BCP Recovery Contacts page with contact type, organisation, contact, title, phone/mobile."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Get all recovery contacts
    contacts = await bcp_repo.list_recovery_contacts(plan["id"])
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Recovery Contacts",
            "plan": plan,
            "contacts": contacts,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/recovery_contacts.html", context)


@router.post("/recovery-contacts", include_in_schema=False)
async def create_recovery_contact_endpoint(
    request: Request,
    org_name: str = Form(...),
    contact_name: str = Form(None),
    title: str = Form(None),
    phone: str = Form(None),
):
    """Create a new recovery contact."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bcp_repo.create_recovery_contact(
        plan["id"],
        org_name,
        contact_name if contact_name else None,
        title if title else None,
        phone if phone else None,
    )
    
    return RedirectResponse(
        url="/bcp/recovery-contacts?success=" + quote("Recovery contact created successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/recovery-contacts/{contact_id}/update", include_in_schema=False)
async def update_recovery_contact_endpoint(
    request: Request,
    contact_id: int,
    org_name: str = Form(...),
    contact_name: str = Form(None),
    title: str = Form(None),
    phone: str = Form(None),
):
    """Update a recovery contact."""
    user, company_id = await _require_bcp_edit(request)
    
    updated = await bcp_repo.update_recovery_contact(
        contact_id,
        org_name=org_name,
        contact_name=contact_name if contact_name else None,
        title=title if title else None,
        phone=phone if phone else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    
    return RedirectResponse(
        url="/bcp/recovery-contacts?success=" + quote("Recovery contact updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/recovery-contacts/{contact_id}/delete", include_in_schema=False)
async def delete_recovery_contact_endpoint(
    request: Request,
    contact_id: int,
):
    """Delete a recovery contact."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_recovery_contact(contact_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    
    return RedirectResponse(
        url="/bcp/recovery-contacts?success=" + quote("Recovery contact deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/recovery-contacts/export", include_in_schema=False)
async def export_recovery_contacts_csv(request: Request):
    """Export recovery contacts to CSV."""
    user, company_id = await _require_bcp_export(request)
    
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    contacts = await bcp_repo.list_recovery_contacts(plan["id"])
    
    # Create CSV
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "ID",
            "Organisation",
            "Contact",
            "Title",
            "Phone/Mobile",
            "Created At",
            "Updated At",
        ],
    )
    writer.writeheader()
    
    for contact in contacts:
        writer.writerow({
            "ID": contact["id"],
            "Organisation": contact["org_name"],
            "Contact": contact.get("contact_name") or "",
            "Title": contact.get("title") or "",
            "Phone/Mobile": contact.get("phone") or "",
            "Created At": contact.get("created_at", ""),
            "Updated At": contact.get("updated_at", ""),
        })
    
    output.seek(0)
    
    from datetime import datetime as dt
    filename = f"recovery_contacts_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============================================================================
# Insurance Claims Page and Endpoints
# ============================================================================


@router.get("/insurance-claims", response_class=HTMLResponse, include_in_schema=False)
async def bcp_insurance_claims(request: Request):
    """BCP Insurance Claims page with insurer, date, claim details, follow-up actions."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Get all insurance claims
    claims = await bcp_repo.list_insurance_claims(plan["id"])
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Insurance Claims",
            "plan": plan,
            "claims": claims,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/insurance_claims.html", context)


@router.post("/insurance-claims", include_in_schema=False)
async def create_insurance_claim_endpoint(
    request: Request,
    insurer: str = Form(...),
    claim_date: str = Form(None),
    details: str = Form(None),
    follow_up_actions: str = Form(None),
):
    """Create a new insurance claim."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Parse date if provided
    claim_date_obj = None
    if claim_date:
        try:
            from datetime import datetime
            claim_date_obj = datetime.fromisoformat(claim_date)
        except ValueError:
            pass
    
    await bcp_repo.create_insurance_claim(
        plan["id"],
        insurer,
        claim_date_obj,
        details if details else None,
        follow_up_actions if follow_up_actions else None,
    )
    
    return RedirectResponse(
        url="/bcp/insurance-claims?success=" + quote("Insurance claim created successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/insurance-claims/{claim_id}/update", include_in_schema=False)
async def update_insurance_claim_endpoint(
    request: Request,
    claim_id: int,
    insurer: str = Form(...),
    claim_date: str = Form(None),
    details: str = Form(None),
    follow_up_actions: str = Form(None),
):
    """Update an insurance claim."""
    user, company_id = await _require_bcp_edit(request)
    
    # Parse date if provided
    claim_date_obj = None
    if claim_date:
        try:
            from datetime import datetime
            claim_date_obj = datetime.fromisoformat(claim_date)
        except ValueError:
            pass
    
    updated = await bcp_repo.update_insurance_claim(
        claim_id,
        insurer=insurer,
        claim_date=claim_date_obj,
        details=details if details else None,
        follow_up_actions=follow_up_actions if follow_up_actions else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")
    
    return RedirectResponse(
        url="/bcp/insurance-claims?success=" + quote("Insurance claim updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/insurance-claims/{claim_id}/delete", include_in_schema=False)
async def delete_insurance_claim_endpoint(
    request: Request,
    claim_id: int,
):
    """Delete an insurance claim."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_insurance_claim(claim_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")
    
    return RedirectResponse(
        url="/bcp/insurance-claims?success=" + quote("Insurance claim deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/insurance-claims/export", include_in_schema=False)
async def export_insurance_claims_csv(request: Request):
    """Export insurance claims to CSV."""
    user, company_id = await _require_bcp_export(request)
    
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    claims = await bcp_repo.list_insurance_claims(plan["id"])
    
    # Create CSV
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "ID",
            "Insurer",
            "Date",
            "Claim Details",
            "Follow-up Actions",
            "Created At",
            "Updated At",
        ],
    )
    writer.writeheader()
    
    for claim in claims:
        writer.writerow({
            "ID": claim["id"],
            "Insurer": claim["insurer"],
            "Date": claim.get("claim_date", ""),
            "Claim Details": claim.get("details") or "",
            "Follow-up Actions": claim.get("follow_up_actions") or "",
            "Created At": claim.get("created_at", ""),
            "Updated At": claim.get("updated_at", ""),
        })
    
    output.seek(0)
    
    from datetime import datetime as dt
    filename = f"insurance_claims_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============================================================================
# Market Changes Page and Endpoints
# ============================================================================


@router.get("/market-changes", response_class=HTMLResponse, include_in_schema=False)
async def bcp_market_changes(request: Request):
    """BCP Market Changes page with market change, impact to business, business options."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    # Get all market changes
    changes = await bcp_repo.list_market_changes(plan["id"])
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Market Assessment",
            "plan": plan,
            "changes": changes,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/market_changes.html", context)


@router.post("/market-changes", include_in_schema=False)
async def create_market_change_endpoint(
    request: Request,
    change: str = Form(...),
    impact: str = Form(None),
    options: str = Form(None),
):
    """Create a new market change record."""
    user, company_id = await _require_bcp_edit(request)
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    await bcp_repo.create_market_change(
        plan["id"],
        change,
        impact if impact else None,
        options if options else None,
    )
    
    return RedirectResponse(
        url="/bcp/market-changes?success=" + quote("Market change created successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/market-changes/{change_id}/update", include_in_schema=False)
async def update_market_change_endpoint(
    request: Request,
    change_id: int,
    change: str = Form(...),
    impact: str = Form(None),
    options: str = Form(None),
):
    """Update a market change record."""
    user, company_id = await _require_bcp_edit(request)
    
    updated = await bcp_repo.update_market_change(
        change_id,
        change=change,
        impact=impact if impact else None,
        options=options if options else None,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market change not found")
    
    return RedirectResponse(
        url="/bcp/market-changes?success=" + quote("Market change updated successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/market-changes/{change_id}/delete", include_in_schema=False)
async def delete_market_change_endpoint(
    request: Request,
    change_id: int,
):
    """Delete a market change record."""
    user, company_id = await _require_bcp_edit(request)
    
    deleted = await bcp_repo.delete_market_change(change_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market change not found")
    
    return RedirectResponse(
        url="/bcp/market-changes?success=" + quote("Market change deleted successfully"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/market-changes/export", include_in_schema=False)
async def export_market_changes_csv(request: Request):
    """Export market changes to CSV."""
    user, company_id = await _require_bcp_export(request)
    
    from fastapi.responses import StreamingResponse
    import csv
    from io import StringIO
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    changes = await bcp_repo.list_market_changes(plan["id"])
    
    # Create CSV
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "ID",
            "Market Change",
            "Impact to Business",
            "Business Options",
            "Created At",
            "Updated At",
        ],
    )
    writer.writeheader()
    
    for change in changes:
        writer.writerow({
            "ID": change["id"],
            "Market Change": change["change"],
            "Impact to Business": change.get("impact") or "",
            "Business Options": change.get("options") or "",
            "Created At": change.get("created_at", ""),
            "Updated At": change.get("updated_at", ""),
        })
    
    output.seek(0)
    
    from datetime import datetime as dt
    filename = f"market_changes_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============================================================================
# Wellbeing Page (Informational)
# ============================================================================


@router.get("/wellbeing", response_class=HTMLResponse, include_in_schema=False)
async def bcp_wellbeing(request: Request):
    """BCP Staff Wellbeing informational page with admin-editable links/phone numbers."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        plan = await bcp_repo.create_plan(company_id)
        await bcp_repo.seed_default_objectives(plan["id"])
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    # Default wellbeing resources
    wellbeing_resources = [
        {
            "title": "Employee Assistance Program (EAP)",
            "description": "Free, confidential counseling and support services for employees and their families.",
            "phone": "1800 EAP HELP",
            "website": "https://www.eap.example.com",
        },
        {
            "title": "Beyond Blue",
            "description": "24/7 support for anyone experiencing anxiety, depression or suicidal thoughts.",
            "phone": "1300 22 4636",
            "website": "https://www.beyondblue.org.au",
        },
        {
            "title": "Lifeline",
            "description": "24-hour crisis support and suicide prevention services.",
            "phone": "13 11 14",
            "website": "https://www.lifeline.org.au",
        },
        {
            "title": "Kids Helpline",
            "description": "Free, private and confidential 24/7 phone and online counselling service for young people aged 5 to 25.",
            "phone": "1800 55 1800",
            "website": "https://kidshelpline.com.au",
        },
        {
            "title": "MensLine Australia",
            "description": "24/7 telephone and online support, information and referral service for men.",
            "phone": "1300 78 99 78",
            "website": "https://mensline.org.au",
        },
        {
            "title": "1800RESPECT",
            "description": "24/7 national sexual assault, domestic and family violence counselling service.",
            "phone": "1800 737 732",
            "website": "https://www.1800respect.org.au",
        },
    ]
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "Staff Wellbeing Support",
            "plan": plan,
            "wellbeing_resources": wellbeing_resources,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/wellbeing.html", context)


# ============================================================================
# Admin: Seeding and Documentation
# ============================================================================


@router.get("/admin/seed-info", response_class=HTMLResponse, include_in_schema=False)
async def bcp_seed_info(request: Request):
    """BCP Seeding Info page - shows what defaults are seeded and how to manage them."""
    user, company_id = await _require_bcp_view(request)
    
    from app.main import _build_base_context
    from app.core.config import get_templates_config
    from fastapi.templating import Jinja2Templates
    from app.services.bcp_seeding import get_seeding_documentation
    
    # Get or create plan for this company
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        from app.services.bcp_seeding import seed_new_plan_defaults
        plan = await bcp_repo.create_plan(company_id)
        await seed_new_plan_defaults(plan["id"])
    
    # Get seeding documentation
    seed_docs = get_seeding_documentation()
    
    templates_config = get_templates_config()
    templates = Jinja2Templates(directory=str(templates_config.template_path))
    
    context = await _build_base_context(
        request,
        user,
        extra={
            "title": "BCP Seed Data Information",
            "plan": plan,
            "seed_docs": seed_docs,
            "can_edit": user.get("is_super_admin") or await membership_repo.user_has_permission(user["id"], "bcp:edit"),
        },
    )
    
    return templates.TemplateResponse("bcp/seed_info.html", context)


@router.post("/admin/reseed", include_in_schema=False)
async def reseed_bcp_defaults(
    request: Request,
    categories: list[str] = Form(default=None),
):
    """Re-seed BCP defaults. Idempotent - only adds missing items."""
    user, company_id = await _require_bcp_edit(request)
    
    from app.services.bcp_seeding import reseed_plan_defaults
    from app.services import audit
    
    plan = await bcp_repo.get_plan_by_company(company_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    
    # Parse categories if provided as a single comma-separated string
    category_list = None
    if categories:
        if isinstance(categories, str):
            category_list = [c.strip() for c in categories.split(",") if c.strip()]
        elif isinstance(categories, list):
            category_list = categories
    
    # Re-seed defaults
    stats = await reseed_plan_defaults(plan["id"], category_list)
    
    # Audit log the re-seeding action
    await audit.log_action(
        action="bcp.admin.reseed",
        user_id=user["id"],
        entity_type="bcp_plan",
        entity_id=plan["id"],
        metadata={
            "company_id": company_id,
            "categories": category_list or "all",
            "items_added": stats,
        },
        request=request,
    )
    
    # Build success message
    total_added = sum(stats.values())
    if total_added == 0:
        message = "No items were added - all requested defaults already exist"
    else:
        parts = []
        if stats.get("objectives"):
            parts.append(f"{stats['objectives']} objectives")
        if stats.get("immediate_checklist"):
            parts.append(f"{stats['immediate_checklist']} immediate checklist items")
        if stats.get("crisis_recovery_checklist"):
            parts.append(f"{stats['crisis_recovery_checklist']} crisis/recovery checklist items")
        if stats.get("emergency_kit_documents") or stats.get("emergency_kit_equipment"):
            kit_total = stats.get("emergency_kit_documents", 0) + stats.get("emergency_kit_equipment", 0)
            parts.append(f"{kit_total} emergency kit items")
        if stats.get("example_risks"):
            parts.append(f"{stats['example_risks']} example risks")
        
        message = f"Successfully added: {', '.join(parts)}"
    
    return RedirectResponse(
        url="/bcp/admin/seed-info?success=" + quote(message),
        status_code=status.HTTP_303_SEE_OTHER,
    )
