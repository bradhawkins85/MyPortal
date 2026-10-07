"""Client onboarding feature pack.

Admins create single-use magic links at ``/admin/client-onboarding``. The
public form at ``/onboarding/{token}`` creates the company, its sites and
contacts, and a support ticket in the ``New Client`` status.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router


PACK = FeaturePack(
    slug="client_onboarding",
    version="1.0.0",
    routers=(router,),
)


__all__ = ["PACK"]
