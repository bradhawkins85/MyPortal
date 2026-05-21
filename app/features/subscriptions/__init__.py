"""Subscriptions feature pack.

Owns the customer portal ``/subscriptions`` routes and the admin
``/admin/subscriptions`` route so they can be hot-reloaded via
``POST /api/features/subscriptions/reload``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .admin_routes import router as admin_router
from .portal_routes import router as portal_router


PACK = FeaturePack(
    slug="subscriptions",
    version="1.0.0",
    routers=(portal_router, admin_router),
)


__all__ = ["PACK"]
