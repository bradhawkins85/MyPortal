"""Assets feature pack.

Owns the assets pages and delete endpoint:

* ``GET /assets``
* ``GET /assets/settings``
* ``GET /assets/{asset_id}``
* ``DELETE /assets/{asset_id}``
* ``GET /network-map`` and its SVG/PDF exports, plus the interface and link
  documentation endpoints under ``/api/network-map``

Handlers are migrated from ``app/main.py`` so they can be hot-reloaded
independently.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .network_map_routes import router as network_map_router
from .routes import router as assets_router


PACK = FeaturePack(
    slug="assets",
    version="1.19.0",
    routers=(assets_router, network_map_router),
)


__all__ = ["PACK"]
