"""Reporting feature pack.

Owns reporting page/admin routes:

* ``GET /reporting``
* ``GET /reporting/{report_id}/export``
* ``GET /admin/reporting``
* ``GET /admin/reporting/new``
* ``GET /admin/reporting/{report_id}/edit``
* ``POST /admin/reporting``
* ``POST /admin/reporting/{report_id}``
* ``POST /admin/reporting/{report_id}/delete``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as reporting_router


PACK = FeaturePack(
    slug="reporting",
    version="1.0.0",
    routers=(reporting_router,),
)


__all__ = ["PACK"]
