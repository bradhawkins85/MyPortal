"""Global and per-company business hours pages."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import Request

from app.security.flash import flash_redirect

_GLOBAL_URL = "/admin/business-hours"


def _main():
    from app import main as main_module

    return main_module


async def company_edit_context(company_id: int) -> dict[str, Any]:
    """Template context for the business hours card on the company edit page."""

    from app.repositories import business_hours as repo
    from app.services import business_hours as service

    company_record = await repo.get_schedule(company_id)
    global_record = await repo.get_schedule(None)
    base_record = company_record or global_record
    return {
        "business_hours_custom": company_record is not None,
        "business_hours_global_configured": global_record is not None,
        "business_hours_rows": service.weekly_form_rows(
            base_record.get("weekly_hours") if base_record else None
        ),
        "business_hours_timezone": (
            company_record.get("timezone") if company_record else _default_timezone()
        ),
        "business_hours_include_global_closures": (
            company_record.get("include_global_closures", True) if company_record else True
        ),
        "business_hours_closures": await repo.list_closures(company_id),
        "business_hours_timezones": service.timezone_options(),
        "business_hours_status": await service.status_for_company(company_id),
    }


def _default_timezone() -> str:
    from app.services import business_hours as service

    return service.default_timezone_name()


def _parse_schedule_form(form: Any) -> tuple[str, dict[str, Any] | None, str | None]:
    from app.services import business_hours as service

    timezone_name = str(form.get("timezone") or "").strip()
    if not service.is_valid_timezone(timezone_name):
        return timezone_name, None, "Select a valid time zone."
    weekly, error = service.parse_weekly_form(form)
    if error:
        return timezone_name, None, error
    return timezone_name, weekly, None


def _parse_closure_form(form: Any) -> tuple[date | None, str, str | None]:
    raw_date = str(form.get("closureDate") or "").strip()
    name = str(form.get("closureName") or "").strip()[:150]
    try:
        closure_date = date.fromisoformat(raw_date)
    except ValueError:
        return None, name, "Enter a valid closure date."
    return closure_date, name, None


async def admin_business_hours_page(request: Request):
    from app.repositories import business_hours as repo
    from app.services import business_hours as service

    user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    record = await repo.get_schedule(None)
    company_overrides = await repo.list_company_schedule_ids()
    extra = {
        "title": "Business hours",
        "business_hours_configured": record is not None,
        "business_hours_rows": service.weekly_form_rows(record.get("weekly_hours") if record else None),
        "business_hours_timezone": _default_timezone(),
        "business_hours_timezone_locked": True,
        "business_hours_timezones": service.timezone_options(),
        "business_hours_closures": await repo.list_closures(None),
        "business_hours_status": await service.status_for_company(None),
        "business_hours_company_override_count": len(company_overrides),
    }
    return await _main()._render_template("admin/business_hours.html", request, user, extra=extra)


async def admin_save_business_hours(request: Request):
    from app.repositories import business_hours as repo
    from app.services import business_hours as service

    _, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    weekly, error = service.parse_weekly_form(form)
    if error:
        return flash_redirect(_GLOBAL_URL, error, "error")
    # The global schedule always follows the portal time zone (CRON_TIMEZONE).
    await repo.save_schedule(None, timezone_name=_default_timezone(), weekly_hours=weekly)
    service.invalidate_cache()
    return flash_redirect(_GLOBAL_URL, "Business hours saved.", "success")


async def admin_add_business_hours_closure(request: Request):
    from app.repositories import business_hours as repo
    from app.services import business_hours as service

    _, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    closure_date, name, error = _parse_closure_form(await request.form())
    if error or closure_date is None:
        return flash_redirect(_GLOBAL_URL, error or "Enter a valid closure date.", "error")
    await repo.add_closure(None, closure_date, name)
    service.invalidate_cache()
    return flash_redirect(_GLOBAL_URL, "Closure added.", "success")


async def admin_delete_business_hours_closure(closure_id: int, request: Request):
    from app.repositories import business_hours as repo
    from app.services import business_hours as service

    _, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    await repo.delete_closure(closure_id, None)
    service.invalidate_cache()
    return flash_redirect(_GLOBAL_URL, "Closure removed.", "success")


async def admin_save_company_business_hours(company_id: int, request: Request):
    from app.repositories import business_hours as repo
    from app.services import business_hours as service

    _, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    if str(form.get("mode") or "").strip().lower() != "custom":
        await repo.delete_schedule(company_id)
        service.invalidate_cache(company_id)
        return _main()._company_edit_redirect(
            company_id=company_id, success="Company now uses the global business hours."
        )
    timezone_name, weekly, error = _parse_schedule_form(form)
    if error:
        return _main()._company_edit_redirect(company_id=company_id, error=error)
    await repo.save_schedule(
        company_id,
        timezone_name=timezone_name,
        weekly_hours=weekly,
        include_global_closures=form.get("includeGlobalClosures") is not None,
    )
    service.invalidate_cache(company_id)
    return _main()._company_edit_redirect(company_id=company_id, success="Business hours saved.")


async def admin_add_company_business_hours_closure(company_id: int, request: Request):
    from app.repositories import business_hours as repo
    from app.services import business_hours as service

    _, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    closure_date, name, error = _parse_closure_form(await request.form())
    if error or closure_date is None:
        return _main()._company_edit_redirect(
            company_id=company_id, error=error or "Enter a valid closure date."
        )
    await repo.add_closure(company_id, closure_date, name)
    service.invalidate_cache(company_id)
    return _main()._company_edit_redirect(company_id=company_id, success="Closure added.")


async def admin_delete_company_business_hours_closure(
    company_id: int, closure_id: int, request: Request
):
    from app.repositories import business_hours as repo
    from app.services import business_hours as service

    _, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    await repo.delete_closure(closure_id, company_id)
    service.invalidate_cache(company_id)
    return _main()._company_edit_redirect(company_id=company_id, success="Closure removed.")
