"""Cart feature pack.

Owns customer-facing cart and orders routes so they can be hot-reloaded
via ``POST /api/features/cart/reload``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as cart_router


PACK = FeaturePack(
    slug="cart",
    version="1.0.0",
    routers=(cart_router,),
)


__all__ = ["PACK"]
