"""Companies feature pack.

Owns the admin ``/admin/companies`` routes so they can be hot-reloaded
via ``POST /api/features/companies/reload``.

The handlers themselves still live in :mod:`app.main`; this pack
re-registers them under its own router so the feature registry can
track them as part of the ``companies`` slug and so the route surface
is visible in the admin Feature Packs catalog.  See
:mod:`app.features.subscriptions` for the same pattern.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .admin_routes import router as admin_router


PACK = FeaturePack(
    slug="companies",
    version="1.0.0",
    routers=(admin_router,),
)


__all__ = ["PACK"]
