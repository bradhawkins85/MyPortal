"""Xero feature pack.

This pack owns the Xero integration routes:

  API routes (prefix ``/api/integration-modules/xero``):
  * ``POST /api/integration-modules/xero/webhook``    – signed invoice webhook ingestion
  * ``POST /api/integration-modules/xero/callback``   – legacy callback ingestion
  * ``GET  /api/integration-modules/xero/callback``   – connectivity probe
  * ``GET  /api/integration-modules/xero/tenants``    – list Xero tenants

  OAuth routes (prefix ``/xero``):
  * ``GET  /xero/connect``   – initiate OAuth2 authorization flow
  * ``GET  /xero/callback``  – handle OAuth2 callback and exchange code
"""

from __future__ import annotations

from app.api.routes.xero import oauth_router, router as xero_router
from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="xero",
    version="1.0.0",
    routers=(xero_router, oauth_router),
)


__all__ = ["PACK"]
