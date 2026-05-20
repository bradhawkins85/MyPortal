"""Knowledge Base feature pack.

Owns the Knowledge Base page routes:

* ``GET /knowledge-base``
* ``GET /knowledge-base/articles/{slug}``
* ``GET /admin/knowledge-base``
* ``GET /admin/knowledge-base/new``
* ``GET /admin/knowledge-base/articles/{slug}``

The JSON API under ``app/api/routes/knowledge_base.py`` remains mounted
directly by ``app.main``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as knowledge_base_router


PACK = FeaturePack(
    slug="knowledge_base",
    version="1.0.0",
    routers=(knowledge_base_router,),
)


__all__ = ["PACK"]
