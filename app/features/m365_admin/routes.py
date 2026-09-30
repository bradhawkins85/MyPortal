"""Technician UI routes for Microsoft 365 spam search and purge."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.repositories import companies as companies_repo
from app.repositories import m365_spam_purge as purge_repo
from app.schemas.m365_spam_purge import SpamPurgeRequestCreate
from app.schemas.m365_out_of_office import OutOfOfficeCreate, OutOfOfficeDisable
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import m365 as m365_service
from app.services import m365_signature_deployment as signature_deploy_service
from app.services import m365_signatures as signatures_service
from app.services import m365_spam_purge as purge_service
from app.services import m365_out_of_office as oof_service


router = APIRouter(tags=["Office365 Spam Purge"])


def _main():
    from app import main as main_module
    return main_module


async def _context(request: Request):
    user, redirect = await _main()._require_menu_page_access(
        request,
        "menu.m365.spam_purge",
        write=True,
        detail="Spam Search & Purge permission required",
    )
    if redirect:
        return None, None, redirect
    company_id = getattr(request.state, "active_company_id", None) or user.get("company_id")
    if company_id is None:
        return user, None, flash_redirect("/", "Select a company first.", "error")
    return user, int(company_id), None


async def _oof_context(request: Request, *, write: bool = False):
    user, redirect = await _main()._require_menu_page_access(
        request,
        "menu.m365.out_of_office",
        write=write,
        detail="Out of Office permission required",
    )
    if redirect:
        return None, None, redirect
    company_id = getattr(request.state, "active_company_id", None) or user.get("company_id")
    if company_id is None:
        return user, None, flash_redirect("/", "Select a company first.", "error")
    return user, int(company_id), None


async def _signature_context(request: Request, *, write: bool = False):
    user, redirect = await _main()._require_menu_page_access(
        request,
        "menu.m365.signatures",
        write=write,
        detail="Signature management permission required",
    )
    if redirect:
        return None, None, redirect
    company_id = getattr(request.state, "active_company_id", None) or user.get("company_id")
    if company_id is None:
        return user, None, flash_redirect("/", "Select a company first.", "error")
    return user, int(company_id), None


def _empty_signature_form() -> dict[str, str]:
    return {
        "slug": "",
        "name": "",
        "description": "",
        "html_content": "",
        "text_content": "",
        "priority": "0",
        "is_default": "",
        "schedule_start_on": "",
        "schedule_end_on": "",
    }


def _optional_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _signature_form_values(record: dict | None = None, *, form=None) -> dict[str, str]:
    if form is not None:
        return {
            "slug": str(form.get("slug") or "").strip(),
            "name": str(form.get("name") or "").strip(),
            "description": str(form.get("description") or "").strip(),
            "html_content": str(form.get("html_content") or ""),
            "text_content": str(form.get("text_content") or ""),
            "priority": str(form.get("priority") or "0").strip() or "0",
            "is_default": "on" if form.get("is_default") == "on" else "",
            "schedule_start_on": str(form.get("schedule_start_on") or "").strip(),
            "schedule_end_on": str(form.get("schedule_end_on") or "").strip(),
        }
    if not record:
        return _empty_signature_form()
    return {
        "slug": str(record.get("slug") or "").strip(),
        "name": str(record.get("name") or "").strip(),
        "description": str(record.get("description") or "").strip(),
        "html_content": str(record.get("html_content") or ""),
        "text_content": str(record.get("text_content") or ""),
        "priority": str(record.get("priority") or 0),
        "is_default": "on" if record.get("is_default") else "",
        "schedule_start_on": record["schedule_start_on"].isoformat() if record.get("schedule_start_on") else "",
        "schedule_end_on": record["schedule_end_on"].isoformat() if record.get("schedule_end_on") else "",
    }


async def _render_signature_form(
    request: Request,
    user: dict,
    company_id: int,
    *,
    template_record: dict | None = None,
    form_values: dict | None = None,
    preview: dict | None = None,
    selected_staff_id: int | None = None,
):
    staff_options = await signatures_service.list_preview_staff(company_id)
    extra = {
        "title": "Signature management",
        "template_record": template_record or {},
        "form_values": form_values or _signature_form_values(template_record),
        "preview": preview,
        "preview_staff": staff_options,
        "selected_staff_id": selected_staff_id,
        "schedule_timezone": signatures_service.get_schedule_timezone_name(),
        "variable_suggestions": await signatures_service.list_variable_suggestions(company_id),
    }
    return await _main()._render_template("m365/signatures_form.html", request, user, extra=extra)


@router.get("/m365/out-of-office", response_class=HTMLResponse)
async def out_of_office_page(request: Request):
    user, company_id, redirect = await _oof_context(request)
    if redirect:
        return redirect
    mailboxes = await oof_service.get_selectable_mailboxes(company_id)
    can_write = await _main()._has_menu_page_access(
        request, user, "menu.m365.out_of_office", write=True
    )
    state_error = None
    try:
        states = await oof_service.get_automatic_replies(company_id)
    except m365_service.M365Error as exc:
        states, state_error = [], str(exc)
    return await _main()._render_template("m365/out_of_office.html", request, user, extra={
        "title": "Out of Office", "mailboxes": mailboxes, "can_write": can_write,
        "states": states, "state_error": state_error, "outcomes": [], "submitted": {},
    })


@router.post("/m365/out-of-office")
async def set_out_of_office(request: Request):
    user, company_id, redirect = await _oof_context(request, write=True)
    if redirect:
        return redirect
    form = await request.form()
    submitted = {
        "mailboxes": form.getlist("mailboxes"),
        "start_time": str(form.get("start_time", "")), "end_time": str(form.get("end_time", "")),
        "internal_message": str(form.get("internal_message", "")),
        "external_message": str(form.get("external_message", "")),
        "same_message": form.get("same_message") == "on",
        "external_audience": str(form.get("external_audience", "none")),
    }
    try:
        if form.get("intent") == "disable":
            payload = OutOfOfficeDisable(mailboxes=form.getlist("mailboxes"))
            results = await oof_service.disable_automatic_replies(company_id, payload)
            action = "disable"
        else:
            payload = OutOfOfficeCreate(**submitted)
            results = await oof_service.set_automatic_replies(company_id, payload)
            action = "schedule"
    except (ValueError, ValidationError) as exc:
        return flash_redirect("/m365/out-of-office", str(exc), "error")
    except m365_service.M365Error as exc:
        return flash_redirect(
            "/m365/out-of-office", f"Microsoft 365 request failed: {exc}", "error"
        )
    failures = [result for result in results if not result["success"]]
    audit_after = {
        "mailboxes": [str(item) for item in payload.mailboxes],
        "success_count": len(results) - len(failures),
        "failure_count": len(failures),
    }
    if isinstance(payload, OutOfOfficeCreate):
        audit_after.update({
            "start_time": payload.start_time.isoformat(),
            "end_time": payload.end_time.isoformat(),
            "external_audience": payload.external_audience,
        })
    await audit_service.record(
        action=f"m365.out_of_office.{action}", request=request, user_id=int(user["id"]),
        entity_type="m365_mailbox", entity_id=None,
        after=audit_after,
    )
    mailboxes = await oof_service.get_selectable_mailboxes(company_id)
    can_write = True
    try:
        states, state_error = await oof_service.get_automatic_replies(company_id), None
    except m365_service.M365Error as exc:
        states, state_error = [], str(exc)
    return await _main()._render_template("m365/out_of_office.html", request, user, extra={
        "title": "Out of Office", "mailboxes": mailboxes, "can_write": can_write,
        "states": states, "state_error": state_error, "outcomes": results,
        "submitted": submitted, "completed_action": action,
    })


@router.get("/m365/signatures", response_class=HTMLResponse)
async def signatures_page(request: Request):
    user, company_id, redirect = await _signature_context(request)
    if redirect:
        return redirect
    templates = await signatures_service.list_templates(company_id)
    current_primary = await signatures_service.get_primary_template(company_id)
    company = await companies_repo.get_company_by_id(company_id) or {}
    can_write = await _main()._has_menu_page_access(
        request, user, "menu.m365.signatures", write=True
    )
    return await _main()._render_template(
        "m365/signatures.html",
        request,
        user,
        extra={
            "title": "Signature management",
            "current_primary": current_primary,
            "schedule_timezone": signatures_service.get_schedule_timezone_name(),
            "schedule_today": signatures_service.current_schedule_date(),
            "templates": templates,
            "classic_outlook_enabled": bool(company.get("classic_outlook_signatures_enabled")),
            "can_write": can_write,
        },
    )


@router.post("/m365/signatures/classic-outlook")
async def set_classic_outlook_signatures(request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    enabled = (await request.form()).get("enabled") == "1"
    await companies_repo.update_company(
        company_id, classic_outlook_signatures_enabled=1 if enabled else 0
    )
    await audit_service.record(
        action="m365.signatures.classic_outlook",
        request=request,
        user_id=int(user["id"]),
        entity_type="company",
        entity_id=company_id,
        after={"classic_outlook_signatures_enabled": enabled},
    )
    message = (
        "Classic Outlook signature sync enabled. Tray agents apply the active template within an hour."
        if enabled
        else "Classic Outlook signature sync disabled. Existing signatures on devices are left in place."
    )
    return flash_redirect("/m365/signatures", message, "success")


@router.get("/m365/signatures/new", response_class=HTMLResponse)
async def signature_new_page(request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    return await _render_signature_form(request, user, company_id)


@router.post("/m365/signatures/new")
async def create_signature_template(request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    form = await request.form()
    form_values = _signature_form_values(form=form)
    preview_staff_id = _optional_int(form.get("preview_staff_id"))
    if form.get("intent") == "preview":
        if not preview_staff_id:
            return flash_redirect("/m365/signatures/new", "Choose a staff member to preview this signature.", "error")
        try:
            preview = await signatures_service.render_preview(
                company_id,
                html_content=form_values["html_content"],
                text_content=form_values["text_content"],
                staff_id=preview_staff_id,
            )
        except (TypeError, ValueError) as exc:
            return flash_redirect("/m365/signatures/new", str(exc), "error")
        return await _render_signature_form(
            request,
            user,
            company_id,
            form_values=form_values,
            preview=preview,
            selected_staff_id=preview_staff_id,
        )
    try:
        created = await signatures_service.create_template(
            company_id=company_id,
            slug=form_values["slug"],
            name=form_values["name"],
            description=form_values["description"] or None,
            html_content=form_values["html_content"],
            text_content=form_values["text_content"] or None,
            priority=form_values["priority"],
            is_default=form_values["is_default"] == "on",
            schedule_start_on=form_values["schedule_start_on"] or None,
            schedule_end_on=form_values["schedule_end_on"] or None,
            user_id=int(user["id"]),
        )
    except ValueError as exc:
        return flash_redirect("/m365/signatures/new", str(exc), "error")
    await audit_service.record(
        action="m365.signatures.create",
        request=request,
        user_id=int(user["id"]),
        entity_type="m365_signature_template",
        entity_id=int(created["id"]),
        after={"slug": created["slug"], "status": created["status"]},
    )
    return flash_redirect(f"/m365/signatures/{created['id']}/edit", "Signature template created.", "success")


@router.get("/m365/signatures/{template_id}/edit", response_class=HTMLResponse)
async def signature_edit_page(template_id: int, request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    template_record = await signatures_service.get_template(company_id, template_id)
    if not template_record:
        return flash_redirect("/m365/signatures", "Signature template not found.", "error")
    return await _render_signature_form(request, user, company_id, template_record=template_record)


@router.post("/m365/signatures/{template_id}/edit")
async def update_signature_template(template_id: int, request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    template_record = await signatures_service.get_template(company_id, template_id)
    if not template_record:
        return flash_redirect("/m365/signatures", "Signature template not found.", "error")
    form = await request.form()
    form_values = _signature_form_values(form=form)
    preview_staff_id = _optional_int(form.get("preview_staff_id"))
    if form.get("intent") == "preview":
        if not preview_staff_id:
            return flash_redirect(f"/m365/signatures/{template_id}/edit", "Choose a staff member to preview this signature.", "error")
        try:
            preview = await signatures_service.render_preview(
                company_id,
                html_content=form_values["html_content"],
                text_content=form_values["text_content"],
                staff_id=preview_staff_id,
            )
        except (TypeError, ValueError) as exc:
            return flash_redirect(f"/m365/signatures/{template_id}/edit", str(exc), "error")
        template_record = dict(template_record)
        template_record.update(form_values)
        return await _render_signature_form(
            request,
            user,
            company_id,
            template_record=template_record,
            form_values=form_values,
            preview=preview,
            selected_staff_id=preview_staff_id,
        )
    try:
        updated = await signatures_service.update_template(
            company_id,
            template_id,
            slug=form_values["slug"],
            name=form_values["name"],
            description=form_values["description"] or None,
            html_content=form_values["html_content"],
            text_content=form_values["text_content"] or None,
            priority=form_values["priority"],
            is_default=form_values["is_default"] == "on",
            schedule_start_on=form_values["schedule_start_on"] or None,
            schedule_end_on=form_values["schedule_end_on"] or None,
            user_id=int(user["id"]),
        )
    except ValueError as exc:
        return flash_redirect(f"/m365/signatures/{template_id}/edit", str(exc), "error")
    if not updated:
        return flash_redirect("/m365/signatures", "Signature template not found.", "error")
    await audit_service.record(
        action="m365.signatures.update",
        request=request,
        user_id=int(user["id"]),
        entity_type="m365_signature_template",
        entity_id=int(updated["id"]),
        after={"slug": updated["slug"], "status": updated["status"]},
    )
    return flash_redirect(f"/m365/signatures/{template_id}/edit", "Signature template updated.", "success")


@router.post("/m365/signatures/{template_id}/clone")
async def clone_signature_template(template_id: int, request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    cloned = await signatures_service.clone_template(company_id, template_id, user_id=int(user["id"]))
    if not cloned:
        return flash_redirect("/m365/signatures", "Signature template not found.", "error")
    return flash_redirect(f"/m365/signatures/{cloned['id']}/edit", "Signature template duplicated.", "success")


@router.post("/m365/signatures/{template_id}/publish")
async def publish_signature_template(template_id: int, request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    updated = await signatures_service.publish_template(company_id, template_id, user_id=int(user["id"]))
    if not updated:
        return flash_redirect("/m365/signatures", "Signature template not found.", "error")
    return flash_redirect(f"/m365/signatures/{template_id}/edit", "Signature template published.", "success")


@router.post("/m365/signatures/{template_id}/disable")
async def disable_signature_template(template_id: int, request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    updated = await signatures_service.disable_template(company_id, template_id, user_id=int(user["id"]))
    if not updated:
        return flash_redirect("/m365/signatures", "Signature template not found.", "error")
    return flash_redirect(f"/m365/signatures/{template_id}/edit", "Signature template disabled.", "success")


async def _render_signature_deploy(
    request: Request,
    user: dict,
    company_id: int,
    template_record: dict,
    *,
    outcomes: list | None = None,
    submitted: dict | None = None,
):
    return await _main()._render_template(
        "m365/signatures_deploy.html",
        request,
        user,
        extra={
            "title": "Deploy signature",
            "template_record": template_record,
            "targets": await signature_deploy_service.list_deployment_targets(company_id),
            "roaming": await signature_deploy_service.get_roaming_signature_status(company_id),
            "max_mailboxes": signature_deploy_service.MAX_DEPLOY_MAILBOXES,
            "outcomes": outcomes or [],
            "submitted": submitted or {},
        },
    )


@router.get("/m365/signatures/{template_id}/deploy", response_class=HTMLResponse)
async def signature_deploy_page(template_id: int, request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    template_record = await signatures_service.get_template(company_id, template_id)
    if not template_record:
        return flash_redirect("/m365/signatures", "Signature template not found.", "error")
    return await _render_signature_deploy(request, user, company_id, template_record)


@router.post("/m365/signatures/{template_id}/deploy")
async def deploy_signature_template(template_id: int, request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    deploy_url = f"/m365/signatures/{template_id}/deploy"
    template_record = await signatures_service.get_template(company_id, template_id)
    if not template_record:
        return flash_redirect("/m365/signatures", "Signature template not found.", "error")
    form = await request.form()
    submitted = {
        "mailboxes": [str(item) for item in form.getlist("mailboxes")],
        "auto_add_new": form.get("auto_add_new") == "on",
        "auto_add_reply": form.get("auto_add_reply") == "on",
    }
    try:
        results = await signature_deploy_service.deploy_template(
            company_id,
            template_id,
            submitted["mailboxes"],
            auto_add_new=submitted["auto_add_new"],
            auto_add_reply=submitted["auto_add_reply"],
        )
    except ValueError as exc:
        return flash_redirect(deploy_url, str(exc), "error")
    except m365_service.M365Error as exc:
        return flash_redirect(deploy_url, f"Microsoft 365 request failed: {exc}", "error")
    failures = [result for result in results if not result["success"]]
    await audit_service.record(
        action="m365.signatures.deploy",
        request=request,
        user_id=int(user["id"]),
        entity_type="m365_signature_template",
        entity_id=template_id,
        after={
            "slug": template_record.get("slug"),
            "mailboxes": [result["mailbox"] for result in results],
            "success_count": len(results) - len(failures),
            "failure_count": len(failures),
            "auto_add_new": submitted["auto_add_new"],
            "auto_add_reply": submitted["auto_add_reply"],
        },
    )
    return await _render_signature_deploy(
        request, user, company_id, template_record, outcomes=results, submitted=submitted
    )


@router.post("/m365/signatures/{template_id}/deploy/postpone-roaming")
async def postpone_roaming_signatures(template_id: int, request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    deploy_url = f"/m365/signatures/{template_id}/deploy"
    try:
        await signature_deploy_service.postpone_roaming_signatures(company_id)
    except m365_service.M365Error as exc:
        return flash_redirect(deploy_url, f"Microsoft 365 request failed: {exc}", "error")
    await audit_service.record(
        action="m365.signatures.postpone_roaming",
        request=request,
        user_id=int(user["id"]),
        entity_type="m365_organization_config",
        entity_id=None,
        after={"PostponeRoamingSignaturesUntilLater": True},
    )
    return flash_redirect(
        deploy_url,
        "Exchange-managed signatures enabled. Outlook clients can take a few hours to pick up the change.",
        "success",
    )


@router.post("/m365/signatures/{template_id}/delete")
async def delete_signature_template(template_id: int, request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    deleted = await signatures_service.delete_template(company_id, template_id)
    if not deleted:
        return flash_redirect("/m365/signatures", "Signature template not found.", "error")
    await audit_service.record(
        action="m365.signatures.delete",
        request=request,
        user_id=int(user["id"]),
        entity_type="m365_signature_template",
        entity_id=template_id,
        after=None,
    )
    return flash_redirect("/m365/signatures", "Signature template deleted.", "success")


@router.get("/m365/spam-purge", response_class=HTMLResponse)
async def spam_purge_page(request: Request):
    user, company_id, redirect = await _context(request)
    if redirect:
        return redirect
    history = await purge_repo.list_requests(company_id)
    return await _main()._render_template("m365/spam_purge.html", request, user, extra={
        "title": "Spam search & purge", "requests": history,
        "active_jobs": any(
            row["search_status"] in {"queued", "starting", "running"}
            or row["purge_status"] in {"queued", "starting", "running"}
            for row in history
        ),
    })


@router.post("/m365/spam-purge/search")
async def create_and_start_search(request: Request):
    user, company_id, redirect = await _context(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        payload = SpamPurgeRequestCreate(
            sender=form.get("sender") or None,
            subject=form.get("subject") or None,
            received_from=date.fromisoformat(str(form["receivedFrom"])) if form.get("receivedFrom") else None,
            received_to=date.fromisoformat(str(form["receivedTo"])) if form.get("receivedTo") else None,
            content_match_query=form.get("contentMatchQuery") or None,
        )
        item = await purge_service.create_request(company_id, int(user["id"]), payload.model_dump())
        await purge_service.start_search(int(item["id"]), company_id)
    except (ValueError, ValidationError, m365_service.M365Error) as exc:
        return flash_redirect("/m365/spam-purge", str(exc), "error")
    await audit_service.record(
        action="m365.spam_search.start", request=request, user_id=int(user["id"]),
        entity_type="m365_spam_purge_request", entity_id=int(item["id"]),
        after={"query": item["content_match_query"]},
    )
    return flash_redirect("/m365/spam-purge", "Compliance search queued.", "success")


@router.post("/m365/spam-purge/{request_id}/purge")
async def purge_search_result(request_id: int, request: Request):
    user, company_id, redirect = await _context(request)
    if redirect:
        return redirect
    confirmation = (await request.form()).get("confirmation", "")
    if str(confirmation).strip().upper() != "PURGE":
        return flash_redirect("/m365/spam-purge", "Type PURGE to confirm permanent deletion.", "error")
    try:
        item = await purge_service.start_purge(request_id, company_id)
    except (LookupError, ValueError, m365_service.M365Error) as exc:
        return flash_redirect("/m365/spam-purge", str(exc), "error")
    await audit_service.record(
        action="m365.spam_purge.start", request=request, user_id=int(user["id"]),
        entity_type="m365_spam_purge_request", entity_id=request_id,
        before={"matched_items": item.get("matched_items")}, after={"purge_type": "HardDelete"},
    )
    return flash_redirect("/m365/spam-purge", "Hard-delete purge queued.", "success")


@router.post("/m365/spam-purge/{request_id}/retry")
async def retry_failed_search(request_id: int, request: Request):
    user, company_id, redirect = await _context(request)
    if redirect:
        return redirect
    try:
        item = await purge_service.start_search(request_id, company_id)
    except (LookupError, ValueError, m365_service.M365Error) as exc:
        return flash_redirect("/m365/spam-purge", str(exc), "error")
    await audit_service.record(
        action="m365.spam_search.retry", request=request, user_id=int(user["id"]),
        entity_type="m365_spam_purge_request", entity_id=request_id,
        before={"search_status": "failed"},
        after={"search_status": item.get("search_status")},
    )
    return flash_redirect("/m365/spam-purge", "Compliance search retry queued.", "success")
