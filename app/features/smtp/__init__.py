"""SMTP feature pack.

Owns SMTP webhook routes:
* ``POST /api/webhooks/smtp2go/events``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as smtp_router


PACK = FeaturePack(
    slug="smtp",
    version="1.0.1",
    routers=(smtp_router,),
)


__all__ = ["PACK"]
