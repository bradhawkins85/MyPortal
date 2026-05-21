"""Assets feature pack.

Owns the assets pages and delete endpoint:

* ``GET /assets``
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
    version="1.0.1",
    routers=(assets_router,),
)


__all__ = ["PACK"]
