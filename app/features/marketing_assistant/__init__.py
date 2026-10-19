"""Marketing Assistant feature pack.

Builds and refreshes marketing page sections, with images, from the websites
an admin lists for each page. Every statement is cross-checked against those
websites before it is used; see :mod:`app.services.marketing_assistant`.

Routes:

* ``GET /admin/marketing/pages/{page_id}/assistant``
* ``GET /admin/marketing/pages/{page_id}/assistant/status``
* ``POST /admin/marketing/pages/{page_id}/assistant/sources``
* ``POST /admin/marketing/assistant/sources/{source_id}/delete``
* ``POST /admin/marketing/pages/{page_id}/assistant/run``
* ``POST /admin/marketing/assistant/runs/{run_id}/restore``
* ``GET /marketing/assistant-images/{image_id}``
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router


PACK = FeaturePack(
    slug="marketing_assistant",
    version="1.0.0",
    routers=(router,),
)


__all__ = ["PACK"]
