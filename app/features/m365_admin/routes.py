"""Technician UI routes for Microsoft 365 spam search and purge."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.repositories import m365_spam_purge as purge_repo
from app.schemas.m365_spam_purge import SpamPurgeRequestCreate
from app.schemas.m365_out_of_office import OutOfOfficeCreate
from app.security.flash import flash_redirect
from app.services import audit as audit_service
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
    }


def _optional_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        "form_values": form_values or template_record or _empty_signature_form(),
        "preview": preview,
        "preview_staff": staff_options,
        "selected_staff_id": selected_staff_id,
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
    return await _main()._render_template("m365/out_of_office.html", request, user, extra={
        "title": "Out of Office", "mailboxes": mailboxes, "can_write": can_write,
    })


@router.post("/m365/out-of-office")
async def set_out_of_office(request: Request):
    user, company_id, redirect = await _oof_context(request, write=True)
    if redirect:
        return redirect
    form = await request.form()
    try:
        payload = OutOfOfficeCreate(
            mailboxes=form.getlist("mailboxes"),
            start_time=datetime.fromisoformat(str(form.get("start_time", "")).replace("Z", "+00:00")),
            end_time=datetime.fromisoformat(str(form.get("end_time", "")).replace("Z", "+00:00")),
            internal_message=form.get("internal_message", ""),
            external_message=form.get("external_message") or None,
            same_message=form.get("same_message") == "on",
        )
        results = await oof_service.set_automatic_replies(company_id, payload)
    except (ValueError, ValidationError) as exc:
        return flash_redirect("/m365/out-of-office", str(exc), "error")
    failures = [result for result in results if not result["success"]]
    await audit_service.record(
        action="m365.out_of_office.set", request=request, user_id=int(user["id"]),
        entity_type="m365_mailbox", entity_id=None,
        after={"mailboxes": [str(item) for item in payload.mailboxes],
               "start_time": payload.start_time.isoformat(), "end_time": payload.end_time.isoformat(),
               "success_count": len(results) - len(failures), "failure_count": len(failures)},
    )
    if failures:
        failed_names = ", ".join(str(item["mailbox"]) for item in failures)
        return flash_redirect("/m365/out-of-office", f"Updated {len(results) - len(failures)} mailbox(es); failed: {failed_names}.", "error")
    return flash_redirect("/m365/out-of-office", f"Out of Office set for {len(results)} mailbox(es).", "success")


@router.get("/m365/signatures", response_class=HTMLResponse)
async def signatures_page(request: Request):
    user, company_id, redirect = await _signature_context(request)
    if redirect:
        return redirect
    templates = await signatures_service.list_templates(company_id)
    return await _main()._render_template(
        "m365/signatures.html",
        request,
        user,
        extra={
            "title": "Signature management",
            "templates": templates,
        },
    )


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
    form_values = {
        "slug": str(form.get("slug") or "").strip(),
        "name": str(form.get("name") or "").strip(),
        "description": str(form.get("description") or "").strip(),
        "html_content": str(form.get("html_content") or ""),
        "text_content": str(form.get("text_content") or ""),
    }
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
    form_values = {
        "slug": str(form.get("slug") or "").strip(),
        "name": str(form.get("name") or "").strip(),
        "description": str(form.get("description") or "").strip(),
        "html_content": str(form.get("html_content") or ""),
        "text_content": str(form.get("text_content") or ""),
    }
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


@router.post("/m365/signatures/{template_id}/delete")
async def delete_signature_template(template_id: int, request: Request):
    user, company_id, redirect = await _signature_context(request, write=True)
    if redirect:
        return redirect
    await signatures_service.delete_template(company_id, template_id)
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
    except (ValueError, ValidationError) as exc:
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
    except (LookupError, ValueError) as exc:
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
    except (LookupError, ValueError) as exc:
        return flash_redirect("/m365/spam-purge", str(exc), "error")
    await audit_service.record(
        action="m365.spam_search.retry", request=request, user_id=int(user["id"]),
        entity_type="m365_spam_purge_request", entity_id=request_id,
        before={"search_status": "failed"},
        after={"search_status": item.get("search_status")},
    )
    return flash_redirect("/m365/spam-purge", "Compliance search retry queued.", "success")
