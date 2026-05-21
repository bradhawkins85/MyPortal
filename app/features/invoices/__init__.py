"""Invoices feature pack.

Owns portal invoice routes so they can be hot-reloaded via
``POST /api/features/invoices/reload``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as invoices_router


PACK = FeaturePack(
    slug="invoices",
    version="1.0.0",
    routers=(invoices_router,),
)


__all__ = ["PACK"]
