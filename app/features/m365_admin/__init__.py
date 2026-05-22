"""M365 Admin feature pack.

This pack makes the ``m365-admin`` integration module a first-class
hot-reloadable feature-pack unit, even though it does not currently
own standalone routes.
"""

from __future__ import annotations

from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="m365_admin",
    version="1.0.0",
)


__all__ = ["PACK"]
