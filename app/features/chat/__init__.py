"""Chat feature pack.

Owns the chat view pages:

* ``GET /chat``
* ``GET /chat/{room_id}``

Handlers are migrated from ``app/main.py`` so they can be hot-reloaded
independently. API routes under ``app/api/routes/chat.py`` remain
mounted directly by ``app/main.py``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as chat_router


PACK = FeaturePack(
    slug="chat",
    version="1.0.0",
    routers=(chat_router,),
)


__all__ = ["PACK"]
