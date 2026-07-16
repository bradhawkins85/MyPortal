"""Calls feature pack."""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as calls_router


PACK = FeaturePack(
    slug="calls",
    version="1.0.0",
    routers=(calls_router,),
)


__all__ = ["PACK"]
