"""Staff feature pack.

Owns the staff page and workflow routes under ``/staff`` and
``/api/staff`` so they can be hot-reloaded independently.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as staff_router


PACK = FeaturePack(
    slug="staff",
    version="1.0.0",
    routers=(staff_router,),
)


__all__ = ["PACK"]
