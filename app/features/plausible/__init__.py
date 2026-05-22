"""Plausible feature pack.

Owns the email-tracking API routes used by Plausible integration:

* ``GET /api/email-tracking/pixel/{tracking_id}.gif``
* ``GET /api/email-tracking/click``
* ``GET /api/email-tracking/status/{tracking_id}``
* ``GET /api/email-tracking/replies/{reply_id}/recipients``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as plausible_router


PACK = FeaturePack(
    slug="plausible",
    version="1.0.0",
    routers=(plausible_router,),
)


__all__ = ["PACK"]
