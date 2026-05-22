"""Orders feature pack.

Owns the customer-facing orders route so it can be hot-reloaded via
``POST /api/features/orders/reload``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as orders_router


PACK = FeaturePack(
    slug="orders",
    version="1.0.0",
    routers=(orders_router,),
)


__all__ = ["PACK"]
