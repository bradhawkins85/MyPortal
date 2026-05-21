"""Tickets feature pack.

This pack owns both **public portal** ticket routes (``/tickets`` …)
and **admin** ticket routes (``/admin/tickets`` …) so they can be
reloaded independently from the rest of the application via
``POST /api/features/tickets/reload``.

The handlers deliberately import a small set of helpers from
``app.main`` (notably ``_require_authenticated_user``,
``_render_portal_tickets_page``, ``_render_portal_ticket_detail`` and
``_is_helpdesk_technician``).  Those helpers carry session resolution,
template rendering and permission-checking logic that the rest of the
application also depends on; relocating them is an explicit follow-up
PR rather than something we want to do mechanically here.  Because the
pack is loaded *after* ``app.main`` has finished importing (see
``app.main.on_startup``), this import order is safe; on hot reload
``importlib.reload`` re-runs these imports and picks up the still-live
functions in ``app.main``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .admin_routes import router as admin_router
from .portal_routes import router as portal_router


PACK = FeaturePack(
    slug="tickets",
    version="1.1.0",
    routers=(portal_router, admin_router),
)


__all__ = ["PACK"]
