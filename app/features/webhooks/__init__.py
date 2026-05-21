"""Webhooks feature pack.

Owns the webhook admin page route:

* ``GET /admin/webhooks``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as webhooks_router


PACK = FeaturePack(
    slug="webhooks",
    version="1.0.0",
    routers=(webhooks_router,),
)


__all__ = ["PACK"]
