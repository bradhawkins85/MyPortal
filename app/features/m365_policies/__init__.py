"""Microsoft 365 Policies feature pack.

Admins group standards into policies, choose report, alert or remediate for
each, and target tenants by licence SKU, company tags or company variables.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .api_routes import router as api_router
from .routes import router as ui_router

PACK = FeaturePack(
    slug="m365_policies",
    version="1.1.0",
    routers=(api_router, ui_router),
)

__all__ = ["PACK"]
