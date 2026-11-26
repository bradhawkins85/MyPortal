"""Customer satisfaction (CSAT) feature pack (:mod:`app.services.csat`).

* ``GET /admin/csat`` CSAT score, response rate, scores per technician and
  company, recent responses and low-score follow-ups; super admins also set
  the survey delay, link expiry, low-score threshold and excluded companies
* ``GET``/``POST /csat/{token}`` the public survey page linked from the email
* ``/api/csat/...`` summary, responses, a ticket's survey, follow-ups and settings

A background job emails the survey once a ticket has been resolved or closed
for the configured delay (on one instance at a time; see
:mod:`app.services.singleton_jobs`).

Disable it with ``DISABLED_FEATURE_PACKS=csat``: no surveys are sent, and the
pages and APIs go away. Settings, surveys and responses stay in the database.
"""

from __future__ import annotations

from app.core.features import FeaturePack
from app.services import csat as csat_service

from .routes import router

PACK = FeaturePack(
    slug="csat",
    version="1.0.0",
    routers=(router,),
    background_jobs=(csat_service.survey_job,),
)

__all__ = ["PACK"]
