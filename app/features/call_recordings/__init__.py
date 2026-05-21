"""Call recordings feature pack.

Owns the admin call recordings route so it can be hot-reloaded via
``POST /api/features/call_recordings/reload``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as call_recordings_router


PACK = FeaturePack(
    slug="call_recordings",
    version="1.0.0",
    routers=(call_recordings_router,),
)


__all__ = ["PACK"]
