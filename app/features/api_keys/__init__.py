"""API keys feature pack.

Owns the admin API key management page routes:

* ``GET /admin/api-keys``
* ``POST /admin/api-keys``
* ``POST /admin/api-keys/update``
* ``POST /admin/api-keys/rotate``
* ``POST /admin/api-keys/delete``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as api_keys_router


PACK = FeaturePack(
    slug="api_keys",
    version="1.0.0",
    routers=(api_keys_router,),
)


__all__ = ["PACK"]
