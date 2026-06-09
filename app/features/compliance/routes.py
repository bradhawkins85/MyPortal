"""Compliance routes for the ``compliance`` feature pack."""

from __future__ import annotations

from functools import lru_cache
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import get_settings
from app.repositories import companies as company_repo
from app.repositories import compliance_checks as cc_repo
from app.repositories import essential8 as essential8_repo
from app.repositories import tickets as tickets_repo
from app.repositories import user_companies as user_company_repo
from app.security.flash import flash_redirect
from app.services import tickets as tickets_service


router = APIRouter(tags=["Compliance"])
settings = get_settings()
_STATUSES_REQUIRING_HELP = {"not_started", "in_progress", "non_compliant"}
_ESSENTIAL8_TICKET_CATEGORY = "essential8"
_ESSENTIAL8_TICKET_MODULE = "compliance"


def _slugify_essential8_element(name: str) -> str:
    normalised = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in normalised)
    return "-".join(part for part in cleaned.split("-") if part)


def _build_essential8_help_url(base_url: str, element_slug: str) -> str:
    if not base_url or not element_slug:
        return base_url
    if "{element}" in base_url:
        return base_url.replace("{element}", element_slug)
    split = urlsplit(base_url)
    query_items = parse_qsl(split.query, keep_blank_values=True)
    query_items = [(key, value) for key, value in query_items if key != "element"]
    query_items.append(("element", element_slug))
    return urlunsplit(
        (split.scheme, split.netloc, split.path, urlencode(query_items), split.fragment)
    )


def _build_essential8_ticket_reference(company_id: int, requirement_id: int) -> str:
    return f"essential8:req:{requirement_id}:company:{company_id}"


def _format_requirement_label(requirement: dict) -> str:
    maturity_level = str(requirement.get("maturity_level") or "").upper() or "ML"
    requirement_order = requirement.get("requirement_order")
    if requirement_order not in (None, ""):
        return f"{maturity_level} requirement {requirement_order}"
    return f"{maturity_level} requirement"


def _build_essential8_ticket_subject(control: dict, requirement: dict) -> str:
    control_name = str(control.get("name") or "Essential 8 control").strip()
    label = _format_requirement_label(requirement)
    return f"Essential 8 implementation request: {control_name} - {label}"[:255]


def _build_essential8_ticket_description(
    *,
    control: dict,
    requirement: dict,
    company: dict | None,
    user: dict,
) -> str:
    company_name = str((company or {}).get("name") or "Unknown company").strip()
    requester_name = str(
        user.get("display_name")
        or user.get("full_name")
        or user.get("name")
        or user.get("username")
        or user.get("email")
        or "Portal user"
    ).strip()
    requester_email = str(user.get("email") or "Not provided").strip()
    return "\n".join(
        [
            "A portal user requested technician assistance to implement an Essential 8 requirement.",
            "",
            f"Company: {company_name}",
            f"Requester: {requester_name}",
            f"Requester email: {requester_email}",
            "",
            f"Control: {control.get('name') or 'Essential 8 control'}",
            f"Control description: {control.get('description') or 'No description provided.'}",
            f"Requirement: {_format_requirement_label(requirement)}",
            f"Requirement details: {requirement.get('description') or 'No requirement details provided.'}",
            "",
            "Requested action: Please contact the requester to plan and implement this Essential 8 item.",
        ]
    )


def _requirement_needs_compliance_help(requirement_status: str | None) -> bool:
    return requirement_status in _STATUSES_REQUIRING_HELP


def _apply_requirement_help_links(
    requirements: list[dict],
    requirement_compliance_map: dict[int, dict],
    requirement_help_links: dict[int, dict],
) -> None:
    for requirement in requirements:
        req_id = requirement.get("id")
        req_compliance = requirement_compliance_map.get(req_id)
        req_status = req_compliance.get("status") if req_compliance else "not_started"
        help_link = requirement_help_links.get(req_id)
        help_url = ""
        if help_link and help_link.get("marketing_page_slug") and help_link.get("marketing_page_is_published"):
            help_url = f"/marketing/{help_link['marketing_page_slug']}"
        requirement["show_compliance_help"] = bool(
            help_url and _requirement_needs_compliance_help(req_status)
        )
        requirement["compliance_help_url"] = help_url if requirement["show_compliance_help"] else ""


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
    user, _membership, company, company_id, redirect = await _load_compliance_context(request)
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
        summary.get("not_started", 0) > 0 or summary.get("in_progress", 0) > 0
    )
    for record in compliance_records:
        record["show_compliance_help"] = False
        record["compliance_help_url"] = ""

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
    user, _membership, company, company_id, redirect = await _load_compliance_context(request)
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
    requirement_help_links = {
        item["requirement_id"]: item
        for item in await essential8_repo.list_requirement_marketing_page_links()
    }
    for key in ("requirements_ml1", "requirements_ml2", "requirements_ml3"):
        _apply_requirement_help_links(
            control_data.get(key, []),
            requirement_compliance_map,
            requirement_help_links,
        )

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


@router.post("/compliance/requirements/{requirement_id}/ticket", response_class=HTMLResponse)
async def compliance_submit_requirement_ticket(request: Request, requirement_id: int):
    user, _membership, company, company_id, redirect = await _load_compliance_context(request)
    if redirect:
        return redirect

    requirement = await essential8_repo.get_essential8_requirement(requirement_id)
    if not requirement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")
    control_id = int(requirement["control_id"])
    control = await essential8_repo.get_essential8_control(control_id)
    if not control:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Control not found")

    external_reference = _build_essential8_ticket_reference(company_id, requirement_id)
    existing_ticket = await tickets_repo.find_open_ticket_by_external_reference(external_reference)
    if existing_ticket:
        ticket_id = existing_ticket.get("id")
        message = "An open ticket already exists for that Essential 8 requirement."
        if ticket_id:
            message = f"An open ticket already exists for that Essential 8 requirement (ticket #{ticket_id})."
        return flash_redirect(f"/compliance/control/{control_id}", message, "info")

    ticket_status = await tickets_service.resolve_status_or_default(None)
    ticket = await tickets_service.create_ticket(
        subject=_build_essential8_ticket_subject(control, requirement),
        description=_build_essential8_ticket_description(
            control=control,
            requirement=requirement,
            company=company,
            user=user,
        ),
        requester_id=int(user["id"]),
        company_id=company_id,
        assigned_user_id=None,
        priority="normal",
        status=ticket_status,
        category=_ESSENTIAL8_TICKET_CATEGORY,
        module_slug=_ESSENTIAL8_TICKET_MODULE,
        external_reference=external_reference,
        trigger_automations=True,
        initial_reply_author_id=int(user["id"]),
        requester_email=str(user.get("email") or "") or None,
    )
    ticket_id = ticket.get("id")
    message = "Ticket submitted. A technician will contact you about this Essential 8 requirement."
    if ticket_id:
        message = f"Ticket #{ticket_id} submitted. A technician will contact you about this Essential 8 requirement."
    return flash_redirect(f"/compliance/control/{control_id}", message, "success")


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
