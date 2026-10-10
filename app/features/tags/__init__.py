"""Tags feature pack.

Labels on assets and companies that automations and script filters target.
Technicians pick tags from a searchable list (and create new ones there);
MyPortal also tags assets automatically, for example Server, Workstation,
Laptop and the operating system family.

* ``GET /api/tags``, ``POST /api/tags``
* ``GET``/``PUT /api/assets/{asset_id}/tags`` and ``/api/companies/{company_id}/tags``
* ``GET /admin/tags`` to rename, recolour and delete tags

Disable it with ``DISABLED_FEATURE_PACKS=tags``: the pickers, the admin page
and the APIs go away. Assigned tags stay in the database.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import refresh_auto_tags_job, router

PACK = FeaturePack(
    slug="tags",
    version="1.0.0",
    routers=(router,),
    background_jobs=(refresh_auto_tags_job,),
)

__all__ = ["PACK"]
