"""Solidtime feature pack."""

from __future__ import annotations

from app.api.routes.solidtime import router as solidtime_router
from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="solidtime",
    version="1.0.0",
    routers=(solidtime_router,),
)


__all__ = ["PACK"]
