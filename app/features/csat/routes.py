"""Routes for customer satisfaction (CSAT) surveys (:mod:`app.services.csat`)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.api.dependencies.auth import require_helpdesk_technician, require_super_admin
from app.repositories import companies as company_repo
from app.repositories import csat as csat_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import csat as csat_service

router = APIRouter(tags=["Customer satisfaction"])

PAGE_PATH = "/admin/csat"


def _main():
    from app import main as main_module

    return main_module


def _optional_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _settings_payload(settings: csat_service.CsatSettings, excluded: list[int]) -> dict[str, Any]:
    return {
        "enabled": settings.enabled,
        "delay_minutes": settings.delay_minutes,
        "low_score_threshold": settings.low_score_threshold,
        "expiry_days": settings.expiry_days,
        "excluded_company_ids": excluded,
    }


# ---------------------------------------------------------------------------
# Admin page
# ---------------------------------------------------------------------------


@router.get(PAGE_PATH, response_class=HTMLResponse, summary="Customer satisfaction report")
async def csat_page(
    request: Request,
    period: str | None = Query(default=None),
    company_id: str | None = Query(default=None),
):
    main = _main()
    user, redirect = await main._require_helpdesk_page(request)
    if redirect:
        return redirect
    selected_company = _optional_int(company_id)
    report = await csat_service.build_report(period=period, company_id=selected_company)
    settings = await csat_service.load_settings()
    excluded = await csat_repo.list_excluded_company_ids()
    companies = await company_repo.list_companies()
    return await main._render_template(
        "admin/csat.html",
        request,
        user,
        extra={
            "title": "Customer satisfaction",
            "report": report,
            "settings": settings,
            "excluded_company_ids": excluded,
            "companies": companies,
            "selected_company_id": selected_company,
            "periods": [("30", "Last 30 days"), ("90", "Last 90 days"), ("365", "Last 12 months")],
            "rating_labels": csat_service.RATING_LABELS,
            "can_manage": bool(user.get("is_super_admin")),
            "max_delay_minutes": csat_service.MAX_DELAY_MINUTES,
            "max_expiry_days": csat_service.MAX_EXPIRY_DAYS,
        },
    )


@router.post(PAGE_PATH + "/settings", response_class=HTMLResponse, summary="Save survey settings")
async def csat_save_settings(request: Request):
    main = _main()
    user, redirect = await main._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    current = await csat_service.load_settings()
    updated = csat_service.parse_settings_form(form, current)
    excluded = [value for value in (_optional_int(raw) for raw in form.getlist("excluded_company_ids")) if value]
    previous_excluded = await csat_repo.list_excluded_company_ids()
    await csat_service.save_settings(updated, excluded)
    await audit_service.log_action(
        action="csat.settings.update",
        user_id=user.get("id"),
        entity_type="csat_settings",
        entity_id=1,
        previous_value=_settings_payload(current, previous_excluded),
        new_value=_settings_payload(updated, sorted(set(excluded))),
        request=request,
    )
    message = "Satisfaction surveys are on." if updated.enabled else "Satisfaction survey settings saved."
    return flash_redirect(PAGE_PATH + "#csat-settings", message, "success")


@router.post(PAGE_PATH + "/{survey_id:int}/follow-up", response_class=HTMLResponse, summary="Mark a follow-up done")
async def csat_close_follow_up(survey_id: int, request: Request):
    main = _main()
    user, redirect = await main._require_helpdesk_page(request)
    if redirect:
        return redirect
    closed = await csat_service.close_follow_up(survey_id, user_id=int(user["id"]))
    if not closed:
        return flash_redirect(PAGE_PATH, "That response has no follow-up to close.", "warning")
    await audit_service.log_action(
        action="csat.follow_up.done",
        user_id=user.get("id"),
        entity_type="ticket_csat_survey",
        entity_id=survey_id,
        request=request,
    )
    return flash_redirect(PAGE_PATH + "#csat-follow-ups", "Follow-up marked as done.", "success")


# ---------------------------------------------------------------------------
# Public survey page
# ---------------------------------------------------------------------------


async def _render_public(
    request: Request,
    *,
    token: str,
    state: str,
    survey: dict[str, Any] | None = None,
    rating: int | None = None,
    comment: str | None = None,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    main = _main()
    context = await main._build_public_context(
        request,
        extra={
            "title": "How did we do?",
            "token": token,
            "state": state,
            "ticket_number": (survey or {}).get("ticket_number") or (survey or {}).get("ticket_id"),
            "ticket_subject": (survey or {}).get("ticket_subject"),
            "rating": rating,
            "comment": comment or "",
            "error": error,
            "rating_labels": csat_service.RATING_LABELS,
            "max_comment_length": csat_service.MAX_COMMENT_LENGTH,
        },
    )
    response = main.templates.TemplateResponse(context["request"], "csat/survey.html", context)
    response.status_code = status_code
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.get("/csat/{token}", response_class=HTMLResponse, include_in_schema=False)
async def public_survey_form(token: str, request: Request, rating: str | None = Query(default=None)):
    # Opening the link only preselects the rating: mail scanners follow links,
    # so the rating is recorded when the requester submits the form.
    try:
        survey = await csat_service.get_open_survey(token)
    except csat_service.SurveyUnavailable as exc:
        return await _render_public(request, token="", state=str(exc), status_code=status.HTTP_404_NOT_FOUND)
    return await _render_public(
        request, token=token, state="open", survey=survey, rating=csat_service.parse_rating(rating)
    )


@router.post("/csat/{token}", response_class=HTMLResponse, include_in_schema=False)
async def public_survey_submit(token: str, request: Request):
    try:
        survey = await csat_service.get_open_survey(token)
    except csat_service.SurveyUnavailable as exc:
        return await _render_public(request, token="", state=str(exc), status_code=status.HTTP_404_NOT_FOUND)
    form = await request.form()
    rating = csat_service.parse_rating(form.get("rating"))
    comment = csat_service.clean_comment(form.get("comment"))
    if rating is None:
        return await _render_public(
            request,
            token=token,
            state="open",
            survey=survey,
            comment=comment,
            error="Choose how satisfied you were.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    recorded = await csat_service.submit_response(survey, rating=rating, comment=comment)
    return await _render_public(request, token="", state="thanks" if recorded else "answered", rating=rating)


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


class CsatSettingsModel(BaseModel):
    enabled: bool = False
    delay_minutes: int = Field(default=csat_service.DEFAULT_DELAY_MINUTES, ge=0, le=csat_service.MAX_DELAY_MINUTES)
    low_score_threshold: int = Field(default=csat_service.DEFAULT_LOW_SCORE_THRESHOLD, ge=0, le=4)
    expiry_days: int = Field(default=csat_service.DEFAULT_EXPIRY_DAYS, ge=1, le=csat_service.MAX_EXPIRY_DAYS)
    excluded_company_ids: list[int] = Field(default_factory=list)


def _serialise_response(item: dict[str, Any]) -> dict[str, Any]:
    def iso(value: Any) -> Any:
        return value.isoformat() if hasattr(value, "isoformat") else value

    return {
        "id": item.get("id"),
        "ticket_id": item.get("ticket_id"),
        "ticket_number": item.get("ticket_number"),
        "ticket_subject": item.get("ticket_subject"),
        "company_id": item.get("company_id"),
        "company_name": item.get("company_name"),
        "technician_user_id": item.get("technician_user_id"),
        "rating": item.get("rating"),
        "comment": item.get("comment"),
        "follow_up_status": item.get("follow_up_status"),
        "responded_at": iso(item.get("responded_at")),
    }


@router.get("/api/csat/summary", summary="Customer satisfaction scores")
async def api_csat_summary(
    period: str | None = Query(default=None, description="30, 90 or 365 days"),
    company_id: int | None = Query(default=None),
    _: dict = Depends(require_helpdesk_technician),
) -> dict[str, Any]:
    report = await csat_service.build_report(period=period, company_id=company_id)
    return {
        "period_days": int(report["period"]),
        "csat_percent": report["csat_percent"],
        "response_rate_percent": report["response_rate"],
        "counts": report["counts"],
        "technicians": [
            {key: row.get(key) for key in ("technician_user_id", "name", "responses", "csat_percent", "average")}
            for row in report["technicians"]
        ],
        "companies": [
            {key: row.get(key) for key in ("company_id", "company_name", "responses", "csat_percent", "average")}
            for row in report["companies"]
        ],
    }


@router.get("/api/csat/responses", summary="Survey responses")
async def api_csat_responses(
    period: str | None = Query(default=None, description="30, 90 or 365 days"),
    company_id: int | None = Query(default=None),
    follow_up_only: bool = Query(default=False),
    _: dict = Depends(require_helpdesk_technician),
) -> list[dict[str, Any]]:
    _, since = csat_service.period_start(period)
    items = await csat_repo.list_responses(since=since, company_id=company_id, follow_up_only=follow_up_only)
    return [_serialise_response(item) for item in items]


@router.get("/api/csat/tickets/{ticket_id}", summary="A ticket's survey")
async def api_csat_ticket(ticket_id: int, _: dict = Depends(require_helpdesk_technician)) -> dict[str, Any]:
    survey = await csat_repo.get_by_ticket(ticket_id)
    if not survey:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No survey for this ticket")
    payload = _serialise_response(survey)
    payload["status"] = survey.get("status")
    payload["sent_at"] = survey["sent_at"].isoformat() if hasattr(survey.get("sent_at"), "isoformat") else None
    return payload


@router.post("/api/csat/responses/{survey_id}/follow-up", summary="Mark a low score's follow-up done")
async def api_csat_close_follow_up(
    survey_id: int, request: Request, user: dict = Depends(require_helpdesk_technician)
) -> dict[str, Any]:
    if not await csat_service.close_follow_up(survey_id, user_id=int(user["id"])):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No open follow-up for this response")
    await audit_service.log_action(
        action="csat.follow_up.done", user_id=user.get("id"), entity_type="ticket_csat_survey",
        entity_id=survey_id, request=request,
    )
    return {"id": survey_id, "follow_up_status": "done"}


@router.get("/api/csat/settings", summary="Survey settings", response_model=CsatSettingsModel)
async def api_csat_settings(_: dict = Depends(require_super_admin)) -> dict[str, Any]:
    settings = await csat_service.load_settings()
    return _settings_payload(settings, await csat_repo.list_excluded_company_ids())


@router.put("/api/csat/settings", summary="Update survey settings", response_model=CsatSettingsModel)
async def api_csat_update_settings(
    payload: CsatSettingsModel, request: Request, user: dict = Depends(require_super_admin)
) -> dict[str, Any]:
    current = await csat_service.load_settings()
    previous_excluded = await csat_repo.list_excluded_company_ids()
    form = {
        "enabled": "1" if payload.enabled else "",
        "delay_minutes": payload.delay_minutes,
        "low_score_threshold": payload.low_score_threshold,
        "expiry_days": payload.expiry_days,
    }
    updated = csat_service.parse_settings_form(form, current)
    excluded = sorted({value for value in payload.excluded_company_ids if value > 0})
    await csat_service.save_settings(updated, excluded)
    await audit_service.log_action(
        action="csat.settings.update",
        user_id=user.get("id"),
        entity_type="csat_settings",
        entity_id=1,
        previous_value=_settings_payload(current, previous_excluded),
        new_value=_settings_payload(updated, excluded),
        request=request,
    )
    return _settings_payload(updated, excluded)


__all__ = ["router"]
