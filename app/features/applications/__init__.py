"""Company application register.

Documents the applications a company owns: type, version, business impact,
importance if unavailable, champion, product key (encrypted at rest) and the
knowledge base articles or external KB links that support it.

* ``GET /applications`` and ``/applications/types``
* ``/api/applications`` for the same records over the REST API

Disable it with ``DISABLED_FEATURE_PACKS=applications``. Records stay in the
database and reappear when the pack is enabled again.
"""
from app.core.features import FeaturePack
from .routes import router, web_router

PACK = FeaturePack(slug="applications", version="1.0.0", routers=(router, web_router))

__all__ = ["PACK"]
