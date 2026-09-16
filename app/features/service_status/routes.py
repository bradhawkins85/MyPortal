"""Public-portal Service Status routes for the ``service_status`` pack.

Mirrors the route that used to live in ``app/main.py``:

* ``GET /service-status`` — service status dashboard for the active
  company.

URL, response class and behaviour are intentionally identical to the
previous in-line handler so external links, bookmarks, and tests keep
working after the migration.  Shared session and template helpers are
imported lazily from ``app.main``; see the pack ``__init__`` and the
``tickets`` pack for the rationale.
"""

from __future__ import annotations

from datetime import date, datetime, time

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.repositories import companies as company_repo
from app.services import service_status as service_status_service


router = APIRouter(tags=["Service Status"])


def _main():
    """Return the ``app.main`` module.

    The helpers we depend on (``_require_authenticated_user`` and
    ``_render_template``) are defined there.  We import lazily so the
    pack file can be imported in isolation by tests without dragging
    in the full app.
    """

    from app import main as main_module

    return main_module


def _to_iso_fallback(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


@router.get("/service-status", response_class=HTMLResponse)
async def service_status_dashboard(request: Request):
    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return redirect
    active_company_id = getattr(request.state, "active_company_id", None)
    try:
        company_id = int(active_company_id) if active_company_id is not None else None
    except (TypeError, ValueError):
        company_id = None
    services = await service_status_service.list_services_for_company(company_id)
    summary = service_status_service.summarise_services(services)
    status_lookup = {entry["value"]: entry for entry in service_status_service.STATUS_DEFINITIONS}
    return await main_module._render_template(
        "service_status/dashboard.html",
        request,
        user,
        extra={
            "title": "Service status",
            "service_status_entries": services,
            "service_status_summary": summary,
            "service_status_definitions": service_status_service.STATUS_DEFINITIONS,
            "service_status_lookup": status_lookup,
        },
    )


@router.get("/service-status/public/{company_id}/{token}", response_class=HTMLResponse)
async def public_service_status_dashboard(request: Request, company_id: int, token: str):
    main_module = _main()
    company = await company_repo.get_company_by_id(company_id)
    if not company or int(company.get("archived") or 0) == 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Status page not found.",
        )
    if not service_status_service.is_valid_public_status_token(
        company_id,
        token,
        seed=service_status_service.public_status_token_seed(company),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Status page not found.",
        )
    services = await service_status_service.list_services_for_company(company_id)
    to_iso = getattr(main_module, "_to_iso", _to_iso_fallback)
    for service in services:
        service["updated_at_iso"] = to_iso(service.get("updated_at"))
    summary = service_status_service.summarise_services(services)
    status_lookup = {entry["value"]: entry for entry in service_status_service.STATUS_DEFINITIONS}
    return await main_module._render_template(
        "service_status/public_dashboard.html",
        request,
        {"id": 0, "is_super_admin": False},
        extra={
            "title": f"{company.get('name') or 'Company'} service status",
            "public_company": company,
            "service_status_entries": services,
            "service_status_summary": summary,
            "service_status_definitions": service_status_service.STATUS_DEFINITIONS,
            "service_status_lookup": status_lookup,
        },
    )
