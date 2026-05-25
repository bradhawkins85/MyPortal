"""M365 Mail feature pack.

Owns the Office 365 mail API and admin mailbox management routes:

* ``/m365-mail/*``
* ``/admin/modules/m365-mail*``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .admin_routes import router as m365_mail_admin_router
from .api_routes import router as m365_mail_api_router


PACK = FeaturePack(
    slug="m365_mail",
    version="1.0.0",
    routers=(m365_mail_api_router, m365_mail_admin_router),
)


__all__ = ["PACK"]
