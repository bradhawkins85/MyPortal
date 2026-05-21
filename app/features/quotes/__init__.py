"""Quotes feature pack.

Owns portal quote routes so they can be hot-reloaded via
``POST /api/features/quotes/reload``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as quotes_router


PACK = FeaturePack(
    slug="quotes",
    version="1.0.0",
    routers=(quotes_router,),
)


__all__ = ["PACK"]
