"""Service contracts and agreements feature pack (:mod:`app.services.service_agreements`).

* ``GET /admin/agreements`` every company's agreements with this period's
  usage, balance, overage and alerts; super admins add and edit them
* ``GET /admin/agreements/{id}`` one agreement: its recent periods and the
  time entries it covered this period
* ``/api/agreements`` list, read, add, update and delete

Agreements only track coverage: invoicing is unchanged. A background job
fires the ``agreements.usage_threshold``, ``agreements.exhausted`` and
``agreements.expiring`` automation events once per period.

Disable it with ``DISABLED_FEATURE_PACKS=agreements``. Agreements stay in the
database.
"""

from __future__ import annotations

from app.core.features import FeaturePack
from app.services import service_agreements

from .routes import router

PACK = FeaturePack(
    slug="agreements",
    version="1.0.0",
    routers=(router,),
    background_jobs=(service_agreements.alert_job,),
)

__all__ = ["PACK"]
