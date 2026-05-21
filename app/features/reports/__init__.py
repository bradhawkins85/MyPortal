"""Reports feature pack.

Owns the report view/admin routes:

* ``GET /reports/company-overview``
* ``GET /reports/company-overview.pdf``
* ``GET /reports/company-overview/settings``
* ``POST /reports/company-overview/settings``
* ``GET /admin/reports/pdf-cover-image``
* ``POST /admin/reports/pdf-cover-image``
* ``POST /admin/reports/pdf-cover-image/delete``
* ``GET /admin/reports/pdf-cover-image/preview``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as reports_router


PACK = FeaturePack(
    slug="reports",
    version="1.0.0",
    routers=(reports_router,),
)


__all__ = ["PACK"]
