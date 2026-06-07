"""Marketing feature pack."""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as marketing_router


PACK = FeaturePack(
    slug="marketing",
    version="1.0.0",
    routers=(marketing_router,),
)


__all__ = ["PACK"]
