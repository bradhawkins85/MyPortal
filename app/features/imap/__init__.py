"""IMAP feature pack.

Owns IMAP API and admin mailbox management routes:

* ``/imap/*``
* ``/admin/modules/imap*``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .api_routes import router as imap_api_router
from .routes import router as imap_admin_router


PACK = FeaturePack(
    slug="imap",
    version="1.0.0",
    routers=(imap_api_router, imap_admin_router),
)


__all__ = ["PACK"]
