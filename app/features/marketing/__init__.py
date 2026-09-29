"""Marketing feature pack."""

from __future__ import annotations

from app.core.features import FeaturePack

from .campaign_routes import router as campaign_router
from .routes import router as marketing_router


PACK = FeaturePack(
    slug="marketing",
    version="1.2.0",
    routers=(campaign_router, marketing_router),
)


__all__ = ["PACK"]
