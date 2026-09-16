"""Admin SMTP2Go analytics and operations routes for the ``smtp`` feature pack."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse

from app.core.logging import log_error
from app.security.flash import flash_redirect
from app.services import automations as automations_service
from app.services import modules as modules_service
from app.services import smtp2go as smtp2go_service

__all__ = ["router"]

router = APIRouter(tags=["SMTP2Go"])


def _main():
    from app import main as main_module

    return main_module


def _coerce_positive_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        resolved = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, resolved)


async def _render_smtp2go_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    main_module = _main()
    module = await modules_service.get_module("smtp2go", redact=False)
    settings = dict((module or {}).get("settings") or {})
    analytics = await smtp2go_service.get_analytics_summary()
    campaigns = smtp2go_service.evaluate_ab_campaigns(settings.get("ab_campaigns"))
    extra = {
        "title": "SMTP2Go operations",
        "smtp2go_module": module or {"slug": "smtp2go", "enabled": False, "settings": settings},
        "smtp2go_settings": settings,
        "smtp2go_analytics": analytics,
        "smtp2go_campaigns": campaigns,
        "smtp2go_trigger_options": [
            option
            for option in automations_service.list_trigger_events()
            if str(option.get("value") or "").startswith("smtp2go.")
        ],
        "success_message": success_message,
        "error_message": error_message,
    }
    response = await main_module._render_template(
        "admin/smtp2go.html", request, user, extra=extra
    )
    response.status_code = status_code
    return response


@router.get("/admin/modules/smtp2go", response_class=HTMLResponse)
async def admin_smtp2go_dashboard(request: Request):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_smtp2go_dashboard(request, current_user)


@router.post("/admin/modules/smtp2go/settings", response_class=HTMLResponse)
async def admin_update_smtp2go_settings(request: Request):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    existing = await modules_service.get_module("smtp2go", redact=False)
    settings = dict((existing or {}).get("settings") or {})

    campaigns_raw = str(form.get("abCampaignsRaw") or "[]").strip() or "[]"
    try:
        campaigns = json.loads(campaigns_raw)
    except json.JSONDecodeError:
        return await _render_smtp2go_dashboard(
            request,
            current_user,
            error_message="A/B campaign JSON is invalid.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not isinstance(campaigns, list):
        return await _render_smtp2go_dashboard(
            request,
            current_user,
            error_message="A/B campaigns must be a JSON array.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    settings.update(
        {
            "manage_url": "/admin/modules/smtp2go",
            "rate_limit_max_retries": _coerce_positive_int(
                form.get("rateLimitMaxRetries"), 3
            ),
            "retry_backoff_seconds": _coerce_positive_int(
                form.get("retryBackoffSeconds"), 60, minimum=1
            ),
            "not_engaged_delay_seconds": _coerce_positive_int(
                form.get("notEngagedDelaySeconds"), 86400
            ),
            "ab_campaigns": campaigns,
        }
    )

    try:
        await modules_service.update_module("smtp2go", settings=settings)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to update SMTP2Go settings", error=str(exc))
        return await _render_smtp2go_dashboard(
            request,
            current_user,
            error_message="Unable to save SMTP2Go settings right now.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return flash_redirect(
        "/admin/modules/smtp2go",
        "SMTP2Go operations settings updated.",
        "success",
    )
