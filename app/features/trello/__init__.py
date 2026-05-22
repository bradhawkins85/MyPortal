"""Trello feature pack.

This pack owns the Trello integration routes:
  - HEAD/GET  /api/integration-modules/trello/webhook  (Trello callback verification)
  - POST      /api/integration-modules/trello/webhook  (event ingestion)
  - POST      /api/integration-modules/trello/boards/{board_id}/register-webhook
"""

from __future__ import annotations

from app.api.routes.trello import router as trello_router
from app.core.features import FeaturePack


PACK = FeaturePack(
    slug="trello",
    version="1.0.0",
    routers=(trello_router,),
)


__all__ = ["PACK"]
