"""Continuity feature pack.

Owns the admin business continuity plan page routes:

* ``GET /admin/business-continuity-plans``
* ``GET /admin/business-continuity-plans/new``
* ``GET /admin/business-continuity-plans/{plan_id}``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as continuity_router


PACK = FeaturePack(
    slug="continuity",
    version="1.0.0",
    routers=(continuity_router,),
)


__all__ = ["PACK"]
