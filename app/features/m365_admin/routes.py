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
from app.services import m365_spam_purge as purge_service
from app.services import m365_out_of_office as oof_service
from app.repositories import m365 as m365_repo


router = APIRouter(tags=["Office365 Spam Purge"])


def _main():
    from app import main as main_module
    return main_module


async def _context(request: Request):
    user, redirect = await _main()._require_helpdesk_page(request)
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


@router.get("/m365/out-of-office", response_class=HTMLResponse)
async def out_of_office_page(request: Request):
    user, company_id, redirect = await _oof_context(request)
    if redirect:
        return redirect
    mailboxes = await m365_repo.get_mailboxes(company_id, "UserMailbox")
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
