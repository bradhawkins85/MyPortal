"""Issue Tracker feature pack.

Owns the admin issue tracker page routes:

* ``GET  /admin/issues``
* ``POST /admin/issues``
* ``POST /admin/issues/{issue_id}/update``
* ``POST /admin/issues/{issue_id}/assignments/{assignment_id}/status``
* ``POST /admin/issues/{issue_id}/assignments/{assignment_id}/delete``

Handlers are migrated from ``app/main.py`` so they can be hot-reloaded
independently. Issue Tracker API routes under ``app/api/routes/issues.py``
remain mounted directly by ``app/main.py``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as issue_tracker_router


PACK = FeaturePack(
    slug="issue_tracker",
    version="1.0.0",
    routers=(issue_tracker_router,),
)


__all__ = ["PACK"]
