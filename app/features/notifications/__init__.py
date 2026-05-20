"""Notifications feature pack.

Owns the notifications dashboard and settings pages:

* ``GET /notifications``
* ``GET /notifications/settings``

Handlers are migrated from ``app/main.py`` so they can be hot-reloaded
independently. API routes under ``app/api/routes/notifications.py``
remain mounted directly by ``app/main.py``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as notifications_router


PACK = FeaturePack(
    slug="notifications",
    version="1.0.0",
    routers=(notifications_router,),
)


__all__ = ["PACK"]
