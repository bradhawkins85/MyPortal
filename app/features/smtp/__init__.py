"""SMTP feature pack.

Owns SMTP webhook and admin routes:
* ``POST /api/webhooks/smtp2go/events``
* ``GET /admin/modules/smtp2go``
* ``POST /admin/modules/smtp2go/settings``
"""

from __future__ import annotations

from app.core.features import FeaturePack
from .admin_routes import router as smtp_admin_router
from .routes import router as smtp_router


PACK = FeaturePack(
    slug="smtp",
    version="1.1.0",
    routers=(smtp_router, smtp_admin_router),
)


__all__ = ["PACK"]
