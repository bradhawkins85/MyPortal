"""Assets feature pack.

Owns the assets pages and delete endpoint:

* ``GET /assets``
* ``GET /assets/{asset_id}``
* ``GET /assets/settings``
* ``DELETE /assets/{asset_id}``

Handlers are migrated from ``app/main.py`` so they can be hot-reloaded
independently.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as assets_router


PACK = FeaturePack(
    slug="assets",
    version="1.0.2",
    routers=(assets_router,),
)


__all__ = ["PACK"]
