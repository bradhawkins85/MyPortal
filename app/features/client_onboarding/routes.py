"""Admin and public routes for client onboarding."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse

from app.repositories import client_onboarding as onboarding_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import business_hours as business_hours_service
from app.services import client_onboarding as onboarding_service

router = APIRouter(tags=["Client Onboarding"])

_ADMIN_URL = "/admin/client-onboarding"


def _main():
    from app import main as main_module

    return main_module


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


async def _render_admin(
    request: Request,
    user: dict[str, Any],
    *,
    new_link: dict[str, Any] | None = None,
    link_form: dict[str, Any] | None = None,
    link_error: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    from app.services import message_templates as message_templates_service

    records = await onboarding_repo.list_recent()
    invitation_template = await message_templates_service.get_template_by_slug(
        onboarding_service.INVITATION_TEMPLATE_SLUG
    )
    for record in records:
        record["effective_status"] = onboarding_service.effective_status(record)
    response = await _main()._render_template(
        "admin/client_onboarding.html",
        request,
        user,
        extra={
            "title": "Client onboarding",
            "onboardings": records,
            "new_link": new_link,
            "default_expiry_days": onboarding_service.DEFAULT_EXPIRY_DAYS,
            "max_expiry_days": onboarding_service.MAX_EXPIRY_DAYS,
            "max_invite_message": onboarding_service.MAX_INVITE_MESSAGE,
            "link_form": link_form or {},
            "link_error": link_error,
            "invitation_template": invitation_template,
        },
    )
    response.status_code = status_code
    return response


@router.get(_ADMIN_URL, response_class=HTMLResponse)
async def admin_client_onboarding_page(request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_admin(request, user)


@router.post(_ADMIN_URL, response_class=HTMLResponse)
async def admin_create_client_onboarding(request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    link_form = {
        "client_name": str(form.get("clientName") or "").strip(),
        "contact_name": str(form.get("contactName") or "").strip(),
        "recipient_email": str(form.get("recipientEmail") or "").strip(),
        "invite_message": str(form.get("inviteMessage") or "").strip(),
        "expiry_days": str(form.get("expiryDays") or ""),
        "send_email": bool(form.get("sendEmail")),
    }
    error = onboarding_service.validate_link_details(
        client_name=link_form["client_name"],
        contact_name=link_form["contact_name"],
        recipient_email=link_form["recipient_email"],
    )
    if error:
        return await _render_admin(
            request, user, link_form=link_form, link_error=error, status_code=status.HTTP_400_BAD_REQUEST
        )
    record, token = await onboarding_service.create_link(
        client_name=link_form["client_name"],
        contact_name=link_form["contact_name"],
        recipient_email=link_form["recipient_email"],
        invite_message=link_form["invite_message"],
        expiry_days=link_form["expiry_days"],
        created_by_user_id=int(user["id"]),
    )
    emailed = False
    if link_form["send_email"] and record.get("recipient_email"):
        emailed = await onboarding_service.send_link_email(record, token, sender=user)
    await audit_service.record(
        action="client_onboarding.create",
        request=request,
        user_id=int(user["id"]),
        entity_type="client_onboarding",
        entity_id=int(record["id"]),
        metadata={
            "client_name": record.get("client_name"),
            "contact_name": record.get("contact_name"),
            "emailed": emailed,
        },
    )
    return await _render_admin(
        request,
        user,
        new_link={
            "url": onboarding_service.onboarding_url(token),
            "record": record,
            "emailed": emailed,
            "email_requested": bool(form.get("sendEmail") and record.get("recipient_email")),
        },
    )


@router.post(_ADMIN_URL + "/{onboarding_id}/regenerate", response_class=HTMLResponse)
async def admin_regenerate_client_onboarding(onboarding_id: int, request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    token = await onboarding_service.regenerate_link(onboarding_id, form.get("expiryDays"))
    if not token:
        return flash_redirect(_ADMIN_URL, "Only links that have not been submitted can be regenerated.", "error")
    record = await onboarding_repo.get_by_id(onboarding_id) or {}
    emailed = False
    if form.get("sendEmail") and record.get("recipient_email"):
        emailed = await onboarding_service.send_link_email(record, token, sender=user)
    await audit_service.record(
        action="client_onboarding.regenerate",
        request=request,
        user_id=int(user["id"]),
        entity_type="client_onboarding",
        entity_id=onboarding_id,
        metadata={"emailed": emailed},
    )
    return await _render_admin(
        request,
        user,
        new_link={
            "url": onboarding_service.onboarding_url(token),
            "record": record,
            "emailed": emailed,
            "email_requested": bool(form.get("sendEmail") and record.get("recipient_email")),
        },
    )


@router.post(_ADMIN_URL + "/{onboarding_id}/revoke", response_class=HTMLResponse)
async def admin_revoke_client_onboarding(onboarding_id: int, request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    if not await onboarding_repo.revoke(onboarding_id):
        return flash_redirect(_ADMIN_URL, "Only links that have not been submitted can be revoked.", "error")
    await audit_service.record(
        action="client_onboarding.revoke",
        request=request,
        user_id=int(user["id"]),
        entity_type="client_onboarding",
        entity_id=onboarding_id,
    )
    return flash_redirect(_ADMIN_URL, "Onboarding link revoked.", "success")


@router.get(_ADMIN_URL + "/{onboarding_id}", response_class=HTMLResponse)
async def admin_client_onboarding_detail(onboarding_id: int, request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    record = await onboarding_repo.get_by_id(onboarding_id)
    if not record:
        return flash_redirect(_ADMIN_URL, "Onboarding not found.", "error")
    record["effective_status"] = onboarding_service.effective_status(record)
    return await _main()._render_template(
        "admin/client_onboarding_detail.html",
        request,
        user,
        extra={
            "title": "Client onboarding",
            "onboarding": record,
            "submission": record.get("submission") or None,
            "weekdays": business_hours_service.WEEKDAYS,
        },
    )


@router.post("/admin/companies/{company_id}/approve", response_class=HTMLResponse)
async def admin_approve_onboarded_company(company_id: int, request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    edit_url = f"/admin/companies/{company_id}/edit"
    if not await onboarding_service.approve_company(company_id, approved_by_user_id=int(user["id"])):
        return flash_redirect(edit_url, "This company is not waiting for approval.", "error")
    await audit_service.record(
        action="client_onboarding.approve",
        request=request,
        user_id=int(user["id"]),
        entity_type="company",
        entity_id=company_id,
    )
    return flash_redirect(edit_url, "Company approved. It is now active and its contacts are enabled.", "success")


@router.post("/admin/companies/{company_id}/decline", response_class=HTMLResponse)
async def admin_decline_onboarded_company(company_id: int, request: Request):
    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    if not await onboarding_service.decline_company(company_id):
        return flash_redirect(
            f"/admin/companies/{company_id}/edit", "This company is not waiting for approval.", "error"
        )
    await audit_service.record(
        action="client_onboarding.decline",
        request=request,
        user_id=int(user["id"]),
        entity_type="company",
        entity_id=company_id,
    )
    return flash_redirect(
        _ADMIN_URL, "Company declined and archived. Its contacts remain disabled.", "success"
    )


# ---------------------------------------------------------------------------
# Public magic-link form
# ---------------------------------------------------------------------------


async def _render_public(
    request: Request,
    *,
    token: str,
    state: str,
    form: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    status_code: int = status.HTTP_200_OK,
):
    context = await _main()._build_public_context(
        request,
        extra={
            "title": "Client onboarding",
            "token": token,
            "state": state,
            "form": form,
            "errors": errors or [],
            "weekdays": business_hours_service.WEEKDAYS,
            "timezones": business_hours_service.timezone_options(),
            "default_timezone": business_hours_service.default_timezone_name(),
            "max_sites": onboarding_service.MAX_SITES,
            "blank_hours": business_hours_service.weekly_form_rows(None),
        },
    )
    response = _main().templates.TemplateResponse(
        context["request"], "onboarding/client_form.html", context
    )
    response.status_code = status_code
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.get("/onboarding/{token}", response_class=HTMLResponse)
async def public_client_onboarding_form(token: str, request: Request):
    try:
        record = await onboarding_service.get_open_onboarding(token)
    except onboarding_service.OnboardingUnavailable as exc:
        return await _render_public(
            request, token="", state=str(exc), status_code=status.HTTP_404_NOT_FOUND
        )
    return await _render_public(
        request,
        token=token,
        state="open",
        form=onboarding_service.form_state(
            None, client_name=record.get("client_name"), contact_name=record.get("contact_name")
        ),
    )


@router.post("/onboarding/{token}", response_class=HTMLResponse)
async def public_client_onboarding_submit(token: str, request: Request):
    try:
        record = await onboarding_service.get_open_onboarding(token)
    except onboarding_service.OnboardingUnavailable as exc:
        return await _render_public(
            request, token="", state=str(exc), status_code=status.HTTP_404_NOT_FOUND
        )
    form = await request.form()
    submission, errors = onboarding_service.parse_submission(form)
    if submission is None:
        return await _render_public(
            request,
            token=token,
            state="open",
            form=onboarding_service.form_state(form),
            errors=errors,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        await onboarding_service.complete_onboarding(record, submission)
    except onboarding_service.DuplicateCompany:
        return await _render_public(
            request,
            token=token,
            state="open",
            form=onboarding_service.form_state(form),
            errors=[
                "A client with this business name is already registered. "
                "Check the name, or contact us if you are already a client."
            ],
            status_code=status.HTTP_409_CONFLICT,
        )
    except onboarding_service.OnboardingUnavailable as exc:
        return await _render_public(
            request, token="", state=str(exc), status_code=status.HTTP_409_CONFLICT
        )
    except Exception:
        # The details are kept on the onboarding record for the team to finish manually.
        return await _render_public(request, token="", state="failed")
    return await _render_public(request, token="", state="complete")


__all__ = ["router"]
