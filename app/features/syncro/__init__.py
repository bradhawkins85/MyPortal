"""Syncro feature pack.

This pack owns Syncro admin import routes:
  - GET /admin/tickets/syncro-import
  - POST /admin/syncro/import-contacts
  - POST /admin/syncro/import-companies
  - POST /admin/syncro/import-tickets
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as syncro_router


PACK = FeaturePack(
    slug="syncro",
    version="1.0.0",
    routers=(syncro_router,),
)


__all__ = ["PACK"]
