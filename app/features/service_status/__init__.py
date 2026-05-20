"""Service Status feature pack.

Owns the public ``GET /service-status`` dashboard route that used to
live inline in ``app/main.py``.  The handler is intentionally a thin
wrapper around :mod:`app.services.service_status` and the shared
``_require_authenticated_user`` / ``_render_template`` helpers in
``app.main``, mirroring the pattern established by the ``tickets``
pack (see ``app/features/tickets/__init__.py``).

Admin-side Service Status routes (``/admin/service-status*``) still
live in ``app/main.py`` and will move in a follow-up PR; URLs and
dependency injection are preserved across the move.

The JSON API router at ``app/api/routes/service_status.py`` is mounted
directly by ``app/main.py`` and is **not** part of this pack: it has
its own reload story via FastAPI's normal startup and is shared with
other consumers.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as portal_router


PACK = FeaturePack(
    slug="service_status",
    version="1.0.0",
    routers=(portal_router,),
)


__all__ = ["PACK"]
