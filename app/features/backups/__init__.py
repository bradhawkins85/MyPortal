"""Backups feature pack.

Owns the admin backup job page routes:

* ``GET  /admin/backup-jobs``
* ``HEAD /admin/backup-jobs``
* ``GET  /admin/backup-summary``
* ``HEAD /admin/backup-summary``
* ``POST /admin/backup-jobs``
* ``POST /admin/backup-jobs/{job_id}``
* ``POST /admin/backup-jobs/{job_id}/delete``
* ``POST /admin/backup-jobs/{job_id}/regenerate-token``

Handlers are migrated from ``app/main.py`` so they can be hot-reloaded
independently. The backup status webhook API route under
``app/api/routes/backup_jobs.py`` remains mounted directly by
``app/main.py``.
"""

from __future__ import annotations

from app.core.features import FeaturePack

from .routes import router as backups_router


PACK = FeaturePack(
    slug="backups",
    version="1.0.0",
    routers=(backups_router,),
)


__all__ = ["PACK"]
