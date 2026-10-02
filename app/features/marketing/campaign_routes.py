"""Marketing email campaign routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.repositories import marketing_campaigns as campaign_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import marketing_campaigns as campaign_service
from app.services import message_templates as message_templates_service

from .routes import _main, _require_marketing_scope

router = APIRouter(tags=["Marketing"])

_PREVIEW_LIMIT = 500


async def _form_options(company_ids: set[int] | None) -> dict[str, Any]:
    return {
        "company_options": await campaign_repo.list_company_options(company_ids),
        "can_target_all_companies": company_ids is None,
        "can_choose_sender": company_ids is None,
        "asset_field_options": await campaign_repo.list_asset_field_options(),
        "product_options": await campaign_repo.list_product_options(),
        "message_template_options": await message_templates_service.list_templates(limit=500),
        "campaign_variables": campaign_service.VARIABLES,
        "default_sender": campaign_service.default_sender(),
    }


async def _render_form(
    request: Request,
    current_user: dict[str, Any],
    company_ids: set[int] | None,
    *,
    campaign: dict[str, Any] | None,
    values: dict[str, Any] | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    values = values or campaign or {
        "category": "updates",
        "business_hours_source": "company",
        "audience": {"company_mode": "all" if company_ids is None else "selected"},
    }
    audience = campaign_service.normalise_audience(values.get("audience"))
    response = await _main()._render_template(
        "admin/marketing_campaign_form.html",
        request,
        current_user,
        extra={
            "title": f"Edit — {campaign['name']}" if campaign else "New campaign",
            "campaign": campaign,
            "campaign_values": values,
            "audience": audience,
            "error_message": error_message,
            **(await _form_options(company_ids)),
        },
    )
    response.status_code = status_code
    return response


def _form_values_for_redisplay(form: Any) -> dict[str, Any]:
    values: dict[str, Any] = {key: form.get(key) for key in form.keys()}
    values["audience"] = campaign_service.audience_from_form(form)
    try:
        values["scheduled_for"] = campaign_service.parse_local_datetime(
            form.get("scheduled_for"), form.get("tz_offset")
        )
    except campaign_service.CampaignError:
        values["scheduled_for"] = None
    return values


async def _get_campaign_or_404(campaign_id: int, company_ids: set[int] | None) -> dict[str, Any]:
    campaign = await campaign_repo.get_campaign(campaign_id)
    if not campaign or not await campaign_service.campaign_in_scope(campaign, company_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    return campaign


@router.get("/admin/marketing/campaigns", response_class=HTMLResponse)
async def admin_marketing_campaigns(request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    return await _main()._render_template(
        "admin/marketing_campaigns.html",
        request,
        current_user,
        extra={
            "title": "Email campaigns",
            "campaigns": [
                campaign
                for campaign in await campaign_repo.list_campaigns()
                if await campaign_service.campaign_in_scope(campaign, company_ids)
            ],
            "opt_outs": await campaign_repo.list_opt_outs(company_ids=company_ids),
        },
    )


@router.get("/admin/marketing/campaigns/new", response_class=HTMLResponse)
async def admin_marketing_new_campaign(request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    return await _render_form(request, current_user, company_ids, campaign=None)


@router.post("/admin/marketing/campaigns", response_class=HTMLResponse)
async def admin_marketing_create_campaign(request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        fields = campaign_service.campaign_fields_from_form(form)
    except campaign_service.CampaignError as exc:
        values = _form_values_for_redisplay(form)
        return await _render_form(
            request, current_user, company_ids, campaign=None, values=values, error_message=str(exc)
        )
    scope_error = await campaign_service.campaign_scope_error(fields, company_ids)
    if scope_error:
        return await _render_form(
            request,
            current_user,
            company_ids,
            campaign=None,
            values=_form_values_for_redisplay(form),
            error_message=scope_error,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    campaign_id = await campaign_repo.create_campaign(fields, created_by=int(current_user["id"]))
    await audit_service.record(
        action="marketing.campaign.create",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="marketing_campaign",
        entity_id=campaign_id,
        metadata={"name": fields["name"], "category": fields["category"]},
    )
    return flash_redirect(
        f"/admin/marketing/campaigns/{campaign_id}",
        "Campaign saved as a draft. Review the recipients before sending.",
        "success",
    )


@router.get("/admin/marketing/campaigns/{campaign_id}", response_class=HTMLResponse)
async def admin_marketing_campaign_detail(campaign_id: int, request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    campaign = await _get_campaign_or_404(campaign_id, company_ids)
    extra: dict[str, Any] = {"title": campaign["name"], "campaign": campaign}
    if campaign.get("status") == "draft":
        resolved = await campaign_service.resolve_audience(campaign, company_ids=company_ids)
        extra.update(
            {
                "audience_recipients": resolved["recipients"][:_PREVIEW_LIMIT],
                "audience_recipient_count": len(resolved["recipients"]),
                "audience_company_count": len(
                    {r.get("company_id") for r in resolved["recipients"] if r.get("company_id")}
                ),
                "audience_excluded": resolved["excluded"][:_PREVIEW_LIMIT],
                "preview_limit": _PREVIEW_LIMIT,
            }
        )
    else:
        extra.update(
            {
                "campaign_stats": await campaign_repo.recipient_stats(campaign_id),
                "campaign_recipients": [
                    recipient
                    for recipient in await campaign_repo.list_recipients(campaign_id)
                    if company_ids is None or recipient.get("company_id") in company_ids
                ],
            }
        )
    return await _main()._render_template(
        "admin/marketing_campaign_detail.html", request, current_user, extra=extra
    )


@router.get("/admin/marketing/campaigns/{campaign_id}/edit", response_class=HTMLResponse)
async def admin_marketing_edit_campaign(campaign_id: int, request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    campaign = await _get_campaign_or_404(campaign_id, company_ids)
    if campaign.get("status") != "draft":
        return flash_redirect(
            f"/admin/marketing/campaigns/{campaign_id}", "Only draft campaigns can be edited.", "error"
        )
    return await _render_form(request, current_user, company_ids, campaign=campaign)


@router.post("/admin/marketing/campaigns/{campaign_id}", response_class=HTMLResponse)
async def admin_marketing_update_campaign(campaign_id: int, request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    campaign = await _get_campaign_or_404(campaign_id, company_ids)
    if campaign.get("status") != "draft":
        return flash_redirect(
            f"/admin/marketing/campaigns/{campaign_id}", "Only draft campaigns can be edited.", "error"
        )
    form = await request.form()
    try:
        fields = campaign_service.campaign_fields_from_form(form)
    except campaign_service.CampaignError as exc:
        values = _form_values_for_redisplay(form)
        return await _render_form(
            request, current_user, company_ids, campaign=campaign, values=values, error_message=str(exc)
        )
    scope_error = await campaign_service.campaign_scope_error(fields, company_ids)
    if scope_error:
        return await _render_form(
            request,
            current_user,
            company_ids,
            campaign=campaign,
            values=_form_values_for_redisplay(form),
            error_message=scope_error,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    await campaign_repo.update_campaign(campaign_id, fields)
    return flash_redirect(f"/admin/marketing/campaigns/{campaign_id}", "Campaign updated.", "success")


@router.get("/admin/marketing/campaigns/{campaign_id}/preview", response_class=HTMLResponse)
async def admin_marketing_campaign_preview(campaign_id: int, request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    campaign = await _get_campaign_or_404(campaign_id, company_ids)
    resolved = await campaign_service.resolve_audience(campaign, company_ids=company_ids)
    recipients = resolved["recipients"]
    wanted = campaign_service.normalise_email(request.query_params.get("email"))
    sample = next((r for r in recipients if r["email"] == wanted), None)
    if sample is None:
        sample = recipients[0] if recipients else {"email": current_user.get("email"), "name": None}
    contact = await campaign_service.contact_for(sample)
    rendered = await campaign_service.render_email(campaign, contact, token="0" * 32)
    return await _main()._render_template(
        "admin/marketing_campaign_preview.html",
        request,
        current_user,
        extra={
            "title": f"Preview — {campaign['name']}",
            "campaign": campaign,
            "preview_recipient": sample,
            "preview_recipients": recipients[:_PREVIEW_LIMIT],
            "rendered_email": rendered,
        },
    )


@router.post("/admin/marketing/campaigns/{campaign_id}/test", response_class=HTMLResponse)
async def admin_marketing_campaign_test(campaign_id: int, request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    campaign = await _get_campaign_or_404(campaign_id, company_ids)
    try:
        await campaign_service.send_test(
            campaign, str(current_user.get("email") or ""), company_ids=company_ids
        )
    except campaign_service.CampaignError as exc:
        return flash_redirect(f"/admin/marketing/campaigns/{campaign_id}", str(exc), "error")
    except Exception as exc:  # pragma: no cover - transport failures surface to the admin
        return flash_redirect(
            f"/admin/marketing/campaigns/{campaign_id}", f"Test email failed: {exc}", "error"
        )
    return flash_redirect(
        f"/admin/marketing/campaigns/{campaign_id}",
        f"Test email sent to {current_user.get('email')}.",
        "success",
    )


@router.post("/admin/marketing/campaigns/{campaign_id}/send", response_class=HTMLResponse)
async def admin_marketing_campaign_send(campaign_id: int, request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    await _get_campaign_or_404(campaign_id, company_ids)
    try:
        queued = await campaign_service.queue_campaign(campaign_id, company_ids=company_ids)
    except campaign_service.CampaignError as exc:
        return flash_redirect(f"/admin/marketing/campaigns/{campaign_id}", str(exc), "error")
    await audit_service.record(
        action="marketing.campaign.send",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="marketing_campaign",
        entity_id=campaign_id,
        metadata={"recipients": queued},
    )
    return flash_redirect(
        f"/admin/marketing/campaigns/{campaign_id}",
        f"Campaign queued for {queued} recipients. Emails go out during each recipient's business hours.",
        "success",
    )


@router.post("/admin/marketing/campaigns/{campaign_id}/cancel", response_class=HTMLResponse)
async def admin_marketing_campaign_cancel(campaign_id: int, request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    await _get_campaign_or_404(campaign_id, company_ids)
    try:
        await campaign_service.cancel_campaign(campaign_id)
    except campaign_service.CampaignError as exc:
        return flash_redirect(f"/admin/marketing/campaigns/{campaign_id}", str(exc), "error")
    await audit_service.record(
        action="marketing.campaign.cancel",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="marketing_campaign",
        entity_id=campaign_id,
    )
    return flash_redirect(
        f"/admin/marketing/campaigns/{campaign_id}", "Campaign cancelled. Unsent emails will not go out.", "success"
    )


@router.post("/admin/marketing/campaigns/{campaign_id}/delete", response_class=HTMLResponse)
async def admin_marketing_campaign_delete(campaign_id: int, request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    campaign = await _get_campaign_or_404(campaign_id, company_ids)
    if campaign.get("status") == "sending":
        return flash_redirect(
            f"/admin/marketing/campaigns/{campaign_id}", "Cancel the campaign before deleting it.", "error"
        )
    await campaign_repo.delete_campaign(campaign_id)
    await audit_service.record(
        action="marketing.campaign.delete",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="marketing_campaign",
        entity_id=campaign_id,
        metadata={"name": campaign.get("name")},
    )
    return flash_redirect("/admin/marketing/campaigns", "Campaign deleted.", "success")


@router.post("/admin/marketing/opt-outs/remove", response_class=HTMLResponse)
async def admin_marketing_remove_opt_out(request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    form = await request.form()
    email = campaign_service.normalise_email(form.get("email"))
    if email and company_ids is not None:
        contacts = await campaign_repo.find_staff_by_emails([email])
        if not any(contact.get("company_id") in company_ids for contact in contacts):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Opt-out not found")
    if email:
        await campaign_repo.remove_opt_out(email, campaign_service.CATEGORY_SALES)
        await audit_service.record(
            action="marketing.opt_out.remove",
            request=request,
            user_id=int(current_user["id"]),
            entity_type="marketing_opt_out",
            metadata={"email": email},
        )
    return flash_redirect("/admin/marketing/campaigns", "Contact will receive sales emails again.", "success")


@router.post("/admin/marketing/opt-outs/add", response_class=HTMLResponse)
async def admin_marketing_add_opt_out(request: Request):
    current_user, company_ids, redirect = await _require_marketing_scope(request)
    if redirect:
        return redirect
    form = await request.form()
    email = campaign_service.normalise_email(form.get("email"))
    if not email:
        return flash_redirect("/admin/marketing/campaigns", "Enter a valid email address to opt out.", "error")
    if company_ids is not None:
        contacts = await campaign_repo.find_staff_by_emails([email])
        if not any(contact.get("company_id") in company_ids for contact in contacts):
            return flash_redirect("/admin/marketing/campaigns", "No contact with that email in your scope.", "error")
    await campaign_repo.add_opt_out(email, campaign_service.CATEGORY_SALES, None)
    await audit_service.record(
        action="marketing.opt_out.add",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="marketing_opt_out",
        metadata={"email": email},
    )
    return flash_redirect("/admin/marketing/campaigns", f"{email} will no longer receive sales emails.", "success")


async def _render_unsubscribe(request: Request, *, recipient: dict[str, Any] | None, done: bool):
    context = await _main()._build_public_context(
        request,
        extra={"title": "Email preferences", "recipient": recipient, "unsubscribed": done},
    )
    return _main().templates.TemplateResponse(context["request"], "marketing/unsubscribe.html", context)


@router.get("/marketing/unsubscribe/{token}", response_class=HTMLResponse)
async def marketing_unsubscribe_page(token: str, request: Request):
    recipient = await campaign_service.get_unsubscribe_recipient(token)
    return await _render_unsubscribe(request, recipient=recipient, done=False)


@router.post("/marketing/unsubscribe/{token}", response_class=HTMLResponse)
async def marketing_unsubscribe(token: str, request: Request):
    recipient = await campaign_service.unsubscribe(token)
    return await _render_unsubscribe(request, recipient=recipient, done=recipient is not None)


__all__ = ["router"]
