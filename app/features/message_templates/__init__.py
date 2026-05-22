"""Message templates feature pack.

Owns the admin message-template page routes so they can be hot-reloaded.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as message_templates_router


PACK = FeaturePack(
    slug="message_templates",
    version="1.0.0",
    routers=(message_templates_router,),
)


__all__ = ["PACK"]
