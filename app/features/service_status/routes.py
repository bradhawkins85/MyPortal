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

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

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
