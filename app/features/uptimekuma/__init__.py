"""Uptime Kuma feature pack.

Owns Uptime Kuma integration routes:
* ``POST /api/integration-modules/uptimekuma/alerts``
* ``GET /api/integration-modules/uptimekuma/alerts``
* ``GET /api/integration-modules/uptimekuma/alerts/{alert_id}``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as uptimekuma_router


PACK = FeaturePack(
    slug="uptimekuma",
    version="1.0.0",
    routers=(uptimekuma_router,),
)


__all__ = ["PACK"]
