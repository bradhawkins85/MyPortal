"""M365 Admin feature pack.

This pack makes the ``m365-admin`` integration module a first-class
hot-reloadable feature-pack unit, even though it does not currently
own standalone routes.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .api_routes import oof_router, router as api_router
from .routes import router as ui_router


PACK = FeaturePack(
    slug="m365_admin",
    version="1.2.3",
    routers=(api_router, oof_router, ui_router),
)


__all__ = ["PACK"]
