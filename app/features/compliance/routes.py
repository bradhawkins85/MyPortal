"""Compliance routes for the ``compliance`` feature pack."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from functools import lru_cache
from html import escape
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.config import get_settings
from app.repositories import companies as company_repo
from app.repositories import compliance_checks as cc_repo
from app.repositories import essential8 as essential8_repo
from app.repositories import smb1001 as smb1001_repo
from app.repositories import tickets as tickets_repo
from app.repositories import users as users_repo
from app.repositories import user_companies as user_company_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import email as email_service
from app.services import message_templates as message_templates_service
from app.services import tickets as tickets_service


router = APIRouter(tags=["Compliance"])
settings = get_settings()
_STATUSES_REQUIRING_HELP = {"not_started", "in_progress", "non_compliant"}
_ESSENTIAL8_TICKET_CATEGORY = "essential8"
_ESSENTIAL8_TICKET_MODULE = "compliance"
_SMB1001_TICKET_CATEGORY = "smb1001"
_ENGAGEMENT_LETTER_TEMPLATE_SLUG = "smb1001-engagement-letter"
_ENGAGEMENT_LETTER_SUBJECT_SLUG = "smb1001-engagement-letter-subject"
_DEFAULT_ENGAGEMENT_LETTER_SUBJECT = "Letter of engagement for IT support - {{ company.name }}"
_DEFAULT_ENGAGEMENT_LETTER = (
    "<p>Dear {{ recipient.name }},</p>"
    "<p>This letter confirms that {{ company.name }} engages Hawkins IT Solutions to provide IT support "
    "on an ad hoc basis, as of {{ letter.date }}.</p>"
    "<p>Under this engagement, Hawkins IT Solutions will, when requested:</p>"
    "<ul><li>support, maintain and secure your computers, servers, network and cloud services;</li>"
    "<li>respond to support requests raised through the portal, by email or by phone;</li>"
    "<li>advise on security improvements, including the SMB1001 cyber security controls.</li></ul>"
    "<p>Work is carried out on request and charged at our standard rates unless a separate agreement "
    "says otherwise. Your staff can reach IT support through the MyPortal customer portal.</p>"
    "<p>Please keep this letter with your compliance records. If you would like to discuss a managed "
    "service agreement, reply to this email.</p>"
    "<p>Kind regards,<br>{{ sender.name }}<br>Hawkins IT Solutions</p>"
)


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
        if help_link and help_link.get("external_url"):
            help_url = str(help_link["external_url"])
        elif help_link and help_link.get("marketing_page_slug") and help_link.get("marketing_page_is_published"):
            help_url = f"/marketing/{help_link['marketing_page_slug']}"
        requirement["show_compliance_help"] = bool(
            help_url and _requirement_needs_compliance_help(req_status)
        )
        requirement["compliance_help_url"] = help_url if requirement["show_compliance_help"] else ""
        requirement["compliance_help_label"] = (
            str((help_link or {}).get("recommendation_name") or "Recommended product or service")
            if requirement["show_compliance_help"] else ""
        )


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
    membership = await main_module._get_effective_company_membership(
        request, user["id"], company_id
    )
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


def _build_smb1001_ticket_reference(company_id: int, control_id: int) -> str:
    return f"smb1001:control:{control_id}:company:{company_id}"


def _build_smb1001_ticket_subject(control: dict) -> str:
    code = str(control.get("code") or "").strip()
    name = str(control.get("name") or "SMB1001 control").strip()
    label = f"{code} {name}".strip()
    return f"SMB1001 implementation request: {label}"[:255]


def _build_smb1001_ticket_description(*, control: dict, tier_name: str, company: dict | None, user: dict) -> str:
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
            "A portal user requested technician assistance to implement an SMB1001 control.",
            "",
            f"Company: {company_name}",
            f"Requester: {requester_name}",
            f"Requester email: {requester_email}",
            "",
            f"Control: {control.get('code') or ''} {control.get('name') or 'SMB1001 control'}".strip(),
            f"Tier: {tier_name}",
            f"Domain: {control.get('domain_label') or control.get('domain') or 'Not specified'}",
            f"Requirement: {control.get('description') or 'No description provided.'}",
            f"How it is checked: {control.get('verification') or 'Not specified.'}",
            "",
            "Requested action: Please contact the requester to plan and implement this SMB1001 control.",
        ]
    )


@router.get("/compliance", response_class=HTMLResponse)
async def compliance_page(request: Request):
    main_module = _main()
    user, membership, company, company_id, redirect = await _load_compliance_context(request)
    if redirect:
        return redirect

    profile = await smb1001_repo.ensure_company_profile(company_id, user_id=user.get("id"))
    overview = await smb1001_repo.get_company_overview(company_id)
    progress = overview["progress"]
    evidence_map = await smb1001_repo.list_evidence_map(company_id)
    help_links = await smb1001_repo.list_help_links()
    controls_by_tier: dict[int, list[dict]] = {}
    for control in overview["controls"]:
        needs_help = control["status"] in smb1001_repo.HELP_STATUSES
        link = help_links.get(int(control["id"])) or {}
        control["show_help"] = needs_help
        control["evidence_files"] = evidence_map.get(int(control["id"]), [])
        control["compliance_help_url"] = link.get("help_url", "") if needs_help else ""
        control["compliance_help_label"] = (
            link.get("recommendation_name") or "Recommended product or service"
        ) if control["compliance_help_url"] else ""
        controls_by_tier.setdefault(int(control["tier_level"]), []).append(control)
    has_essential8_records = await essential8_repo.company_has_recorded_progress(company_id)
    essential8_import_count = await smb1001_repo.count_importable_essential8_controls(company_id)

    extra = {
        "title": "SMB1001 Compliance",
        "profile": overview.get("profile") or profile,
        "progress": progress,
        "tiers": progress["tiers"],
        "controls_by_tier": controls_by_tier,
        "domains": smb1001_repo.DOMAINS,
        "company_members": await users_repo.list_users_for_company(company_id),
        "company": company,
        "has_essential8_records": has_essential8_records,
        "essential8_import_count": essential8_import_count,
        "is_super_admin": bool(user.get("is_super_admin")),
        "current_user_id": user.get("id"),
        "can_manage": bool(user.get("is_super_admin")) or bool(membership and membership.get("is_admin")),
    }
    return await main_module._render_template("compliance/smb1001.html", request, user, extra=extra)


def _safe_attestation_filename(company_name: str) -> str:
    safe_name = "".join(
        character if character.isalnum() or character in ("-", "_") else "_"
        for character in company_name.strip().replace(" ", "_")
    ).strip("_")
    safe_name = re.sub(r"_+", "_", safe_name) or "company"
    return f"SMB1001_attestation_{safe_name}_{datetime.now(timezone.utc):%Y%m%d}.pdf"


@router.get("/compliance/attestation-report.pdf", summary="Export the current SMB1001 attestation report")
async def smb1001_attestation_report(request: Request):
    """Export the company's current SMB1001 position and supporting evidence."""
    user, _membership, company, company_id, redirect = await _load_compliance_context(request)
    if redirect:
        return redirect
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    try:
        from weasyprint import HTML  # type: ignore
    except (ImportError, OSError) as exc:  # pragma: no cover - system dependency
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF export is temporarily unavailable.",
        ) from exc

    await smb1001_repo.ensure_company_profile(company_id, user_id=user.get("id"))
    overview = await smb1001_repo.get_company_overview(company_id)
    evidence_map = await smb1001_repo.list_evidence_map(company_id)
    controls = []
    for control in overview["controls"]:
        item = dict(control)
        item["record"] = control.get("compliance") or {}
        item["evidence_files"] = evidence_map.get(int(control["id"]), [])
        controls.append(item)

    generated_at = datetime.now(timezone.utc)
    template = _main().templates.get_template("compliance/smb1001_attestation_pdf.html")
    rendered_html = template.render(
        company=company,
        progress=overview["progress"],
        controls=controls,
        generated_at=generated_at,
    )
    pdf_bytes = await asyncio.to_thread(
        lambda: HTML(string=rendered_html, base_url=str(request.base_url)).write_pdf()
    )
    await audit_service.record(
        action="smb1001.attestation_report.export_pdf",
        request=request,
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        metadata={
            "company_id": company_id,
            "achieved_tier": (overview["progress"].get("achieved_tier") or {}).get("name"),
            "format": "pdf",
        },
    )
    filename = _safe_attestation_filename(str(company.get("name") or f"company_{company_id}"))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/compliance/smb1001/{control_id}/ticket", response_class=HTMLResponse)
async def compliance_submit_smb1001_ticket(request: Request, control_id: int):
    user, _membership, company, company_id, redirect = await _load_compliance_context(request)
    if redirect:
        return redirect

    control = await smb1001_repo.get_control(control_id)
    if not control:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Control not found")
    tiers = {int(tier["tier_level"]): tier for tier in await smb1001_repo.list_tiers()}
    tier_name = str((tiers.get(int(control["tier_level"])) or {}).get("name") or f"Tier {control['tier_level']}")

    external_reference = _build_smb1001_ticket_reference(company_id, control_id)
    existing_ticket = await tickets_repo.find_open_ticket_by_external_reference(external_reference)
    if existing_ticket:
        ticket_id = existing_ticket.get("id")
        message = "An open ticket already exists for that SMB1001 control."
        if ticket_id:
            message = f"An open ticket already exists for that SMB1001 control (ticket #{ticket_id})."
        return flash_redirect("/compliance", message, "info")

    ticket_status = await tickets_service.resolve_status_or_default(None)
    ticket = await tickets_service.create_ticket(
        subject=_build_smb1001_ticket_subject(control),
        description=_build_smb1001_ticket_description(
            control=control,
            tier_name=tier_name,
            company=company,
            user=user,
        ),
        requester_id=int(user["id"]),
        company_id=company_id,
        assigned_user_id=None,
        priority="normal",
        status=ticket_status,
        category=_SMB1001_TICKET_CATEGORY,
        module_slug=_ESSENTIAL8_TICKET_MODULE,
        external_reference=external_reference,
        trigger_automations=True,
        initial_reply_author_id=int(user["id"]),
        requester_email=str(user.get("email") or "") or None,
    )
    ticket_id = ticket.get("id")
    await audit_service.record(
        action="smb1001.help_ticket.create",
        request=request,
        user_id=int(user["id"]),
        entity_type="ticket",
        entity_id=int(ticket_id) if ticket_id else None,
        metadata={"company_id": company_id, "control_id": control_id, "control_code": control.get("code")},
    )
    message = "Ticket submitted. A technician will contact you about this SMB1001 control."
    if ticket_id:
        message = f"Ticket #{ticket_id} submitted. A technician will contact you about this SMB1001 control."
    return flash_redirect("/compliance", message, "success")


def _person_name(person: dict) -> str:
    full_name = " ".join(
        part for part in (str(person.get("first_name") or "").strip(), str(person.get("last_name") or "").strip()) if part
    )
    return full_name or str(person.get("display_name") or person.get("email") or "").strip()


def _letter_html_to_text(html: str) -> str:
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<\s*li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|ul|ol)\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.replace("&nbsp;", " ").strip()


async def _render_engagement_letter(context: dict) -> tuple[str, str, str]:
    """Render the editable letter of engagement as (subject, html, text)."""

    subject, _subject_type = await message_templates_service.render_template_content(
        _ENGAGEMENT_LETTER_SUBJECT_SLUG,
        context,
        default_content=_DEFAULT_ENGAGEMENT_LETTER_SUBJECT,
        default_content_type="text/plain",
    )
    subject = " ".join(subject.split())[:255] or "Letter of engagement for IT support"
    rendered, content_type = await message_templates_service.render_template_content(
        _ENGAGEMENT_LETTER_TEMPLATE_SLUG,
        context,
        default_content=_DEFAULT_ENGAGEMENT_LETTER,
        default_content_type="text/html",
    )
    if content_type == "text/html":
        return subject, rendered, _letter_html_to_text(rendered)
    html = "<p>" + escape(rendered).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    return subject, html, rendered


@router.post("/compliance/smb1001/engagement-letter", response_class=HTMLResponse)
async def compliance_send_engagement_letter(request: Request):
    """Email a letter of engagement to an ad hoc customer to evidence TM-01."""
    user, membership, company, company_id, redirect = await _load_compliance_context(request)
    if redirect:
        return redirect
    if not (user.get("is_super_admin") or (membership and membership.get("is_admin"))):
        return flash_redirect("/compliance", "Administrator access is required to send a letter of engagement.", "error")
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if int(company.get("is_vip") or 0):
        return flash_redirect(
            "/compliance",
            "This company is covered by a managed service agreement, so no letter of engagement is needed.",
            "info",
        )
    control = await smb1001_repo.get_control_by_code(smb1001_repo.ENGAGEMENT_CONTROL_CODE)
    if not control:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Control not found")

    form = await request.form()
    try:
        recipient_id = int(str(form.get("recipient_user_id") or ""))
    except ValueError:
        recipient_id = None
    members = await users_repo.list_users_for_company(company_id)
    recipient = next((member for member in members if int(member["id"]) == recipient_id), None)
    recipient_email = str((recipient or {}).get("email") or "").strip()
    if not recipient_email:
        return flash_redirect("/compliance", "Choose a company member to receive the letter of engagement.", "error")

    today = date.today()
    context = {
        "recipient": {"name": _person_name(recipient), "email": recipient_email},
        "company": {"name": str(company.get("name") or "").strip()},
        "sender": {"name": _person_name(user) or "Hawkins IT Solutions", "email": str(user.get("email") or "")},
        "letter": {"date": today.strftime("%d %B %Y").lstrip("0")},
    }
    subject, html_body, text_body = await _render_engagement_letter(context)
    try:
        sent, _metadata = await email_service.send_email(
            subject=subject,
            recipients=[recipient_email],
            html_body=html_body,
            text_body=text_body,
        )
    except email_service.EmailDispatchError:
        sent = False
    if not sent:
        return flash_redirect("/compliance", "The letter of engagement could not be sent. Check the email settings and try again.", "error")

    control_id = int(control["id"])
    existing = await smb1001_repo.get_company_control_compliance(company_id, control_id) or {}
    evidence_line = f"Letter of engagement emailed to {recipient_email} on {today.isoformat()}."
    previous_evidence = str(existing.get("evidence") or "").strip()
    record = await smb1001_repo.save_company_control_compliance(
        company_id,
        control_id,
        user_id=user.get("id"),
        source="engagement_letter",
        action="engagement_letter_sent",
        change_summary=evidence_line,
        status="compliant",
        evidence=f"{previous_evidence}\n{evidence_line}" if previous_evidence else evidence_line,
        last_reviewed_date=today,
    )
    await audit_service.record(
        action="smb1001.engagement_letter.send",
        request=request,
        user_id=user.get("id"),
        entity_type="smb1001_control_compliance",
        entity_id=record.get("id"),
        before=existing or None,
        after=record,
        metadata={"company_id": company_id, "control_id": control_id, "recipient_user_id": recipient_id},
    )
    return flash_redirect("/compliance", f"Letter of engagement sent to {recipient_email}. TM-01 is now marked compliant.", "success")


@router.get("/compliance/essential8", response_class=HTMLResponse)
async def essential8_legacy_page(request: Request):
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
        "title": "Essential 8 Compliance (legacy)",
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
    evidence_map = await essential8_repo.list_requirement_evidence_map(
        company_id,
        control_id=control_id,
    )
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
    company_members = await users_repo.list_users_for_company(company_id)

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
        "requirement_evidence_map": evidence_map,
        "company_members": company_members,
        "reminder_summary": await essential8_repo.get_requirement_reminder_summary(company_id),
        "trend_rows": await essential8_repo.get_requirement_trend(company_id, control_id=control_id),
        "company": company,
        "is_super_admin": bool(user.get("is_super_admin")),
        "can_manage": bool(user.get("is_super_admin")) or bool(_membership and _membership.get("is_admin")),
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
    membership = await main_module._get_effective_company_membership(
        request, user["id"], company_id
    )
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
