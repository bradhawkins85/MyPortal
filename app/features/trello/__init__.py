"""Trello feature pack.

This pack owns the Trello integration routes:
  - HEAD/GET  /api/integration-modules/trello/webhook  (Trello callback verification)
  - POST      /api/integration-modules/trello/webhook  (event ingestion)
  - POST      /api/integration-modules/trello/boards/{board_id}/register-webhook
  - GET       /trello/connect?company_id=…  (start OAuth 2.0 authorization)
  - GET       /trello/callback              (OAuth 2.0 redirect URI)
  - POST      /trello/companies/{company_id}/disconnect
"""

from __future__ import annotations

from app.api.routes.trello import oauth_router as trello_oauth_router
from app.api.routes.trello import router as trello_router
from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="trello",
    version="1.1.0",
    routers=(trello_router, trello_oauth_router),
)


__all__ = ["PACK"]
