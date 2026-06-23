"""TacticalRMM feature pack.

This pack owns TacticalRMM-specific admin synchronisation routes:
  - POST /admin/modules/tacticalrmm/push-companies
  - POST /admin/modules/tacticalrmm/pull-companies
  - POST /admin/modules/tacticalrmm/sync-tray-tokens
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as tacticalrmm_router

PACK = FeaturePack(
    slug="tacticalrmm",
    version="1.0.0",
    routers=(tacticalrmm_router,),
)


__all__ = ["PACK"]
