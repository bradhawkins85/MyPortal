"""Shop feature pack.

Owns customer and admin Shop routes so they can be hot-reloaded via
``POST /api/features/shop/reload``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as shop_router


PACK = FeaturePack(
    slug="shop",
    version="1.0.0",
    routers=(shop_router,),
)


__all__ = ["PACK"]
